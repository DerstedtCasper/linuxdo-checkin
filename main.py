"""
cron: 0 */6 * * *
new Env("Linux.Do 签到")
"""

import json
import os
import random
import time
import functools
from typing import Dict, List, Optional
from loguru import logger
from DrissionPage import ChromiumOptions, Chromium
from tabulate import tabulate
from curl_cffi import requests
from bs4 import BeautifulSoup
from notify import NotificationManager


def retry_decorator(retries=3, min_delay=5, max_delay=10):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == retries - 1:  # 最后一次尝试
                        logger.error(f"函数 {func.__name__} 最终执行失败: {str(e)}")
                    logger.warning(
                        f"函数 {func.__name__} 第 {attempt + 1}/{retries} 次尝试失败: {str(e)}"
                    )
                    if attempt < retries - 1:
                        sleep_s = random.uniform(min_delay, max_delay)
                        logger.info(
                            f"将在 {sleep_s:.2f}s 后重试 ({min_delay}-{max_delay}s 随机延迟)"
                        )
                        time.sleep(sleep_s)
            return None

        return wrapper

    return decorator


os.environ.pop("DISPLAY", None)
os.environ.pop("DYLD_LIBRARY_PATH", None)

USERNAME = os.environ.get("LINUXDO_USERNAME")
PASSWORD = os.environ.get("LINUXDO_PASSWORD")
COOKIES = os.environ.get("LINUXDO_COOKIES", "").strip()  # 手动设置的 Cookie 字符串，优先使用
BROWSE_ENABLED = os.environ.get("BROWSE_ENABLED", "true").strip().lower() not in [
    "false",
    "0",
    "off",
]
LOGIN_RETRY_COUNT = max(int(os.environ.get("LOGIN_RETRY_COUNT", "3").strip()), 1)
LOGIN_RETRY_MIN_DELAY = max(
    float(os.environ.get("LOGIN_RETRY_MIN_DELAY", "8").strip()), 1.0
)
LOGIN_RETRY_MAX_DELAY = max(
    float(os.environ.get("LOGIN_RETRY_MAX_DELAY", "20").strip()),
    LOGIN_RETRY_MIN_DELAY,
)
try:
    BROWSE_TOPIC_COUNT = int(os.environ.get("BROWSE_TOPIC_COUNT", "3").strip())
    if BROWSE_TOPIC_COUNT < 1:
        raise ValueError("BROWSE_TOPIC_COUNT must be >= 1")
except Exception:
    BROWSE_TOPIC_COUNT = 3
if not USERNAME:
    USERNAME = os.environ.get("USERNAME")
if not PASSWORD:
    PASSWORD = os.environ.get("PASSWORD")

HOME_URL = "https://linux.do/"
LOGIN_URL = "https://linux.do/login"
SESSION_URL = "https://linux.do/session"
CSRF_URL = "https://linux.do/session/csrf"


class LinuxDoBrowser:
    def __init__(self) -> None:
        from sys import platform

        if platform == "linux" or platform == "linux2":
            platformIdentifier = "X11; Linux x86_64"
        elif platform == "darwin":
            platformIdentifier = "Macintosh; Intel Mac OS X 10_15_7"
        elif platform == "win32":
            platformIdentifier = "Windows NT 10.0; Win64; x64"
        else:
            platformIdentifier = "X11; Linux x86_64"

        co = (
            ChromiumOptions()
            .headless(True)
            .incognito(True)
            .set_argument("--no-sandbox")
        )
        co.set_user_agent(
            f"Mozilla/5.0 ({platformIdentifier}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        )
        self.browser = Chromium(co)
        self.page = self.browser.new_tab()
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36 Edg/142.0.0.0",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Accept-Language": "zh-CN,zh;q=0.9",
            }
        )
        # 初始化通知管理器
        self.notifier = NotificationManager()

    def _retry_sleep(self, attempt: int, reason: str) -> None:
        if attempt >= LOGIN_RETRY_COUNT:
            return
        sleep_s = random.uniform(LOGIN_RETRY_MIN_DELAY, LOGIN_RETRY_MAX_DELAY)
        logger.warning(
            f"{reason}; retry {attempt + 1}/{LOGIN_RETRY_COUNT} after {sleep_s:.2f}s"
        )
        time.sleep(sleep_s)

    def _sync_session_cookies_to_browser(self) -> None:
        dp_cookies = []
        for name, value in self.session.cookies.get_dict().items():
            dp_cookies.append(
                {
                    "name": name,
                    "value": value,
                    "domain": ".linux.do",
                    "path": "/",
                }
            )
        if dp_cookies:
            self.page.set.cookies(dp_cookies)

    def _sync_browser_cookies_to_session(self) -> None:
        for ck in self.page.cookies(all_domains=True, all_info=True):
            name = ck.get("name")
            value = ck.get("value")
            if not name or value is None:
                continue
            domain = ck.get("domain") or "linux.do"
            path = ck.get("path") or "/"
            self.session.cookies.set(name, value, domain=domain, path=path)

    def _verify_login(self, source: str) -> bool:
        logger.info(f"{source}: verify login status on linux.do")
        self.page.get(HOME_URL)
        time.sleep(5)
        self._sync_browser_cookies_to_session()
        try:
            user_ele = self.page.ele("@id=current-user")
        except Exception as e:
            logger.warning(f"{source}: verify exception: {e}")
            return "avatar" in self.page.html
        if user_ele:
            logger.info(f"{source}: verify success")
            return True
        if "avatar" in self.page.html:
            logger.info(f"{source}: verify success via avatar")
            return True
        logger.error(f"{source}: verify failed, current-user not found")
        return False

    def _fetch_csrf_from_browser(self) -> Optional[str]:
        logger.info("Load login page in browser and extract CSRF token...")
        self.page.get(LOGIN_URL)
        time.sleep(random.uniform(2, 4))
        csrf_token = self.page.run_js(
            """
            return document.querySelector('meta[name="csrf-token"]')?.content
                || document.querySelector('input[name="authenticity_token"]')?.value
                || null;
            """
        )
        self._sync_browser_cookies_to_session()
        if csrf_token:
            logger.info(f"Browser CSRF token acquired: {csrf_token[:10]}...")
            return csrf_token
        logger.warning("Browser page did not expose a CSRF token")
        return None

    def _fetch_csrf_from_api(self, headers: Dict[str, str]) -> Optional[str]:
        logger.info("Fallback to API CSRF token request...")
        resp_csrf = self.session.get(CSRF_URL, headers=headers, impersonate="firefox135")
        if resp_csrf.status_code != 200:
            logger.error(f"API CSRF request failed: {resp_csrf.status_code}")
            return None
        csrf_data = resp_csrf.json()
        csrf_token = csrf_data.get("csrf")
        if csrf_token:
            logger.info(f"API CSRF token acquired: {csrf_token[:10]}...")
        else:
            logger.error("API response did not include csrf")
        return csrf_token

    def _browser_form_login(self) -> bool:
        logger.info("Falling back to browser form login...")
        try:
            self.page.get(LOGIN_URL)
            time.sleep(random.uniform(2, 4))
            payload = json.dumps({"username": USERNAME, "password": PASSWORD}, ensure_ascii=False)
            script = """
                const payload = __PAYLOAD__;
                const loginInput = document.querySelector('input[name="login"]');
                const passwordInput = document.querySelector('input[name="password"]');
                if (!loginInput || !passwordInput) return false;

                loginInput.focus();
                loginInput.value = payload.username;
                loginInput.dispatchEvent(new Event('input', { bubbles: true }));
                loginInput.dispatchEvent(new Event('change', { bubbles: true }));

                passwordInput.focus();
                passwordInput.value = payload.password;
                passwordInput.dispatchEvent(new Event('input', { bubbles: true }));
                passwordInput.dispatchEvent(new Event('change', { bubbles: true }));

                const submitButton = document.querySelector('button[type="submit"], .btn-primary, .login-button');
                if (submitButton) {
                    submitButton.click();
                    return true;
                }

                const form = loginInput.closest('form');
                if (form) {
                    form.submit();
                    return true;
                }
                return false;
            """.replace('__PAYLOAD__', payload)
            submitted = self.page.run_js(script)
        except Exception as exc:
            logger.error(f"Browser form login failed before submit: {exc}")
            return False

        if not submitted:
            logger.error("Browser form login could not find the login form")
            return False

        time.sleep(random.uniform(6, 9))
        self._sync_browser_cookies_to_session()
        return self._verify_login("browser form login")

    @staticmethod
    def parse_cookie_string(cookie_str: str) -> List[Dict[str, str]]:
        """
        解析浏览器复制的 Cookie 字符串格式: "name1=value1; name2=value2"
        返回 DrissionPage 所需的 cookie 列表格式。
        """
        cookies = []
        for part in cookie_str.strip().split(";"):
            part = part.strip()
            if "=" in part:
                name, _, value = part.partition("=")
                cookies.append(
                    {
                        "name": name.strip(),
                        "value": value.strip(),
                        "domain": ".linux.do",
                        "path": "/",
                    }
                )
        return cookies

    def login_with_cookies(self, cookie_str: str) -> bool:
        """Use a raw cookie string for login and skip password auth."""
        logger.info("Manual cookie detected; trying cookie login...")
        dp_cookies = self.parse_cookie_string(cookie_str)
        if not dp_cookies:
            logger.error("Cookie parsing failed or cookie payload is empty")
            return False

        logger.info(f"Parsed {len(dp_cookies)} cookie entries")

        for ck in dp_cookies:
            self.session.cookies.set(ck["name"], ck["value"], domain="linux.do")

        self.page.set.cookies(dp_cookies)
        return self._verify_login("cookie login")

    def login(self):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36 Edg/142.0.0.0",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": LOGIN_URL,
        }
        data = {
            "login": USERNAME,
            "password": PASSWORD,
            "second_factor_method": "1",
            "timezone": "Asia/Shanghai",
        }
        for attempt in range(1, LOGIN_RETRY_COUNT + 1):
            logger.info(f"Start password login attempt {attempt}/{LOGIN_RETRY_COUNT}")
            csrf_token = self._fetch_csrf_from_browser()
            if not csrf_token:
                csrf_token = self._fetch_csrf_from_api(headers)
            if not csrf_token:
                if self._browser_form_login():
                    return True
                self._retry_sleep(attempt, "Failed to acquire CSRF token")
                continue

            login_headers = {
                **headers,
                "X-CSRF-Token": csrf_token,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": "https://linux.do",
            }
            try:
                resp_login = self.session.post(
                    SESSION_URL,
                    data=data,
                    impersonate="chrome136",
                    headers=login_headers,
                )
            except Exception as exc:
                logger.error(f"Login request error: {exc}")
                if self._browser_form_login():
                    return True
                self._retry_sleep(attempt, "Login request error")
                continue

            if resp_login.status_code == 200:
                response_json = resp_login.json()
                if response_json.get("error"):
                    logger.error(f"Login failed: {response_json.get('error')}")
                    return False
                logger.info("Password login succeeded, syncing cookies...")
                self._sync_session_cookies_to_browser()
                return self._verify_login("password login")

            logger.error(f"Login failed, status code: {resp_login.status_code}")
            if resp_login.status_code in (403, 429):
                if self._browser_form_login():
                    return True
                self._retry_sleep(attempt, f"Site returned {resp_login.status_code}")
                continue
            logger.error(resp_login.text)
            return False
        return False

    def click_topic(self):
        topic_list = self.page.ele("@id=list-area").eles(".:title")
        if not topic_list:
            logger.error("未找到主题帖")
            return False
        topic_count = min(BROWSE_TOPIC_COUNT, len(topic_list))
        logger.info(f"发现 {len(topic_list)} 个主题帖，随机选择{topic_count}个")
        for topic in random.sample(topic_list, topic_count):
            self.click_one_topic(topic.attr("href"))
        return True

    @retry_decorator()
    def click_one_topic(self, topic_url):
        new_page = self.browser.new_tab()
        try:
            new_page.get(topic_url)
            if random.random() < 0.3:  # 0.3 * 30 = 9
                self.click_like(new_page)
            self.browse_post(new_page)
        finally:
            try:
                new_page.close()
            except Exception:
                pass

    def browse_post(self, page):
        prev_url = None
        # 开始自动滚动，最多滚动10次
        for _ in range(10):
            # 随机滚动一段距离
            scroll_distance = random.randint(550, 650)  # 随机滚动 550-650 像素
            logger.info(f"向下滚动 {scroll_distance} 像素...")
            page.run_js(f"window.scrollBy(0, {scroll_distance})")
            logger.info(f"已加载页面: {page.url}")

            if random.random() < 0.03:  # 33 * 4 = 132
                logger.success("随机退出浏览")
                break

            # 检查是否到达页面底部
            at_bottom = page.run_js(
                "window.scrollY + window.innerHeight >= document.body.scrollHeight"
            )
            current_url = page.url
            if current_url != prev_url:
                prev_url = current_url
            elif at_bottom and prev_url == current_url:
                logger.success("已到达页面底部，退出浏览")
                break

            # 动态随机等待
            wait_time = random.uniform(2, 4)  # 随机等待 2-4 秒
            logger.info(f"等待 {wait_time:.2f} 秒...")
            time.sleep(wait_time)

    def run(self):
        try:
            if COOKIES:
                login_res = self.login_with_cookies(COOKIES)
                if not login_res:
                    logger.warning("Cookie login failed, falling back to password login...")
                    login_res = self.login()
            else:
                login_res = self.login()

            if not login_res:
                logger.warning("Login was not established; skip browse and connect info steps")
                return

            if BROWSE_ENABLED:
                click_topic_res = self.click_topic()
                if not click_topic_res:
                    logger.error("Browse step failed because no topic list was available")
                    return
                logger.info("Browse task finished")

            self.print_connect_info()
            self.send_notifications(BROWSE_ENABLED)
        finally:
            try:
                self.page.close()
            except Exception:
                pass
            try:
                self.browser.quit()
            except Exception:
                pass

    def click_like(self, page):
        try:
            # 专门查找未点赞的按钮
            like_button = page.ele(".discourse-reactions-reaction-button")
            if like_button:
                logger.info("找到未点赞的帖子，准备点赞")
                like_button.click()
                logger.info("点赞成功")
                time.sleep(random.uniform(1, 2))
            else:
                logger.info("帖子可能已经点过赞了")
        except Exception as e:
            logger.error(f"点赞失败: {str(e)}")

    def print_connect_info(self):
        logger.info("获取连接信息")
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        }
        resp = self.session.get(
            "https://connect.linux.do/", headers=headers, impersonate="chrome136"
        )
        soup = BeautifulSoup(resp.text, "html.parser")
        rows = soup.select("table tr")
        info = []

        for row in rows:
            cells = row.select("td")
            if len(cells) >= 3:
                project = cells[0].text.strip()
                current = cells[1].text.strip() if cells[1].text.strip() else "0"
                requirement = cells[2].text.strip() if cells[2].text.strip() else "0"
                info.append([project, current, requirement])

        logger.info("--------------Connect Info-----------------")
        logger.info("\n" + tabulate(info, headers=["项目", "当前", "要求"], tablefmt="pretty"))

    def send_notifications(self, browse_enabled):
        """发送签到通知"""
        status_msg = f"✅每日登录成功: {USERNAME}"
        if browse_enabled:
            status_msg += " + 浏览任务完成"
        
        # 使用通知管理器发送所有通知
        self.notifier.send_all("LINUX DO", status_msg)


if __name__ == "__main__":
    if not COOKIES and (not USERNAME or not PASSWORD):
        print("请设置 LINUXDO_COOKIES（Cookie 登录），或同时设置 USERNAME 和 PASSWORD（账号密码登录）")
        exit(1)
    browser = LinuxDoBrowser()
    browser.run()
