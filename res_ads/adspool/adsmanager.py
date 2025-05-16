import requests
import logging
import time
from selenium import webdriver
from selenium.common.exceptions import WebDriverException, InvalidSessionIdException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

logger = logging.getLogger(__name__)

class AdsPowerDriverManager:
    def __init__(self, user_id, api_key, api_host="http://local.adspower.net:50325"):
        self.user_id = user_id
        self.api_key = api_key
        self.api_host = api_host
        self.driver = None
        self.webdriver_path = None
        self.debugger_address = None

    def start_browser(self):
        """
        启动 AdsPower 浏览器实例并初始化 WebDriver。
        """
        open_url = f"{self.api_host}/api/v1/browser/start?user_id={self.user_id}&api_key={self.api_key}"
        try:
            resp = requests.get(open_url).json()
            if resp.get("code") != 0:
                logger.error(f"启动浏览器失败，user_id: {self.user_id}, 错误信息: {resp.get('msg')}")
                return False

            self.webdriver_path = resp["data"]["webdriver"]
            self.debugger_address = resp["data"]["ws"]["selenium"]

            chrome_options = Options()
            chrome_options.add_experimental_option("debuggerAddress", self.debugger_address)
            chrome_options.add_argument("--disable-gpu")

            service = Service(executable_path=self.webdriver_path)
            self.driver = webdriver.Chrome(service=service, options=chrome_options)
            self.driver.set_page_load_timeout(300)
            logger.info(f"成功启动浏览器，user_id: {self.user_id}")
            return True
        except Exception as e:
            logger.error(f"启动浏览器时发生异常，user_id: {self.user_id}, 异常信息: {e}")
            return False

    def is_driver_valid(self):
        """
        检查当前 WebDriver 是否有效。
        """
        if self.driver is None:
            return False
        try:
            # 尝试获取当前窗口句柄以验证会话是否有效
            _ = self.driver.current_window_handle
            return True
        except (InvalidSessionIdException, WebDriverException):
            logger.warning(f"WebDriver 会话无效，user_id: {self.user_id}")
            return False

    def restart_browser(self):
        """
        重新启动浏览器实例。
        """
        self.stop_browser()
        time.sleep(2)  # 等待浏览器完全关闭
        return self.start_browser()

    def stop_browser(self):
        """
        停止 AdsPower 浏览器实例并关闭 WebDriver。
        """
        close_url = f"{self.api_host}/api/v1/browser/stop?user_id={self.user_id}&api_key={self.api_key}"
        try:
            if self.driver:
                try:
                    # 尝试逐一关闭所有窗口
                    for handle in self.driver.window_handles:
                        self.driver.switch_to.window(handle)
                        self.driver.close()
                except Exception as e:
                    logger.warning(f"关闭窗口时发生异常：{e}")
                finally:
                    try:
                        # 尝试关闭 WebDriver 会话
                        self.driver.quit()
                    except Exception as e:
                        logger.warning(f"关闭 WebDriver 会话时发生异常：{e}")
                    finally:
                        self.driver = None

            resp = requests.get(close_url).json()
            if resp.get("code") != 0:
                logger.error(f"关闭浏览器失败，user_id: {self.user_id}, 错误信息: {resp.get('msg')}")
                return False
            logger.info(f"成功关闭浏览器，user_id: {self.user_id}")
            return True
        except Exception as e:
            logger.error(f"关闭浏览器时发生异常，user_id: {self.user_id}, 异常信息: {e}")
            return False
