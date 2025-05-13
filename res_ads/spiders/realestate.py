import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor

import redis
import scrapy
import requests
import time
import sys
import re
from urllib.parse import urljoin

from scrapy import Selector
from scrapy.utils.project import get_project_settings
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
from selenium.webdriver.common.action_chains import ActionChains

from res_ads.adspool.adsmanager import AdsPowerDriverManager
from res_ads.adspool.driverpool import AdsWebDriverPool
from res_ads.db.listing_utils import ListingHelper

# from res_ads.cache import url_queue
from res_ads.items import CombinedRealEstateItem
from res_ads.utils.getredis import get_redis_client
from scrapy_redis.spiders import RedisSpider

# from res_ads.settings import REDIS_URL
# from scrapy_redis.spiders import RedisSpider
settings = get_project_settings()


logger = logging.getLogger('realestate')

class RealestateSpider(RedisSpider):
    name = "realestate"
    allowed_domains = ["realestate.com.au","reastatic.net"]
    start_urls = ["https://www.realestate.com.au/buy/list-1?activeSort=list-date"]
    redis_key = 'realestate_spider:start_urls'
    # redis_key = 'realestate_spider:testurls'
    retry_redis_key = 'realestate_spider:retry_urls'
    BASE_DOMAIN = "https://realestate.com.au"
    js = "window.scrollTo(0, document.body.scrollHeight)"
    js_top = "window.scrollTo({ top: 0, behavior: 'smooth' });"

    # scrapy crawl realestate -a data='{"user": "kxsovgc"]}'
    def __init__(self, *args, **kwargs):
        data = kwargs.pop('data', None)
        super().__init__(*args, **kwargs)

        self.r = get_redis_client()

        try:
            self.data = json.loads(data) if data else {}
        except json.JSONDecodeError as e:
            raise ValueError(f"data 参数 JSON 格式错误: {e}")

        self.user = self.data.get('user', '')
        logger.info(f"ads: {self.user}")

        if not self.user:
            raise ValueError("ads user_ids is None")

        self.manager = AdsPowerDriverManager(user_id=self.user, api_key=settings.get('ADS_API_KEY',''))
        self.manager.start_browser()


    def __del__(self):
        self.manager.stop_browser()

    def safe_get(self, driver, url: str, retries: int = 6, delay: int = 5) -> bool:
        """
        尝试加载页面，若发生 TimeoutException，则重试指定次数。
        :param driver: Selenium WebDriver 实例
        :param url: 要加载的 URL
        :param retries: 最大重试次数（至少为 1）
        :param delay: 每次重试前的等待时间（秒）
        :return: True 表示加载成功，False 表示加载失败
        """
        if retries < 1:
            raise ValueError("retries 必须至少为 1")

        driver.set_page_load_timeout(300)  # 设置页面加载超时时间为300秒

        for attempt in range(1, retries + 1):
            try:
                driver.get(url)
                return True
            except (TimeoutException, WebDriverException) as e:
                logging.warning(f"第 {attempt} 次尝试加载 {url} 时发生异常: {e}")
                if attempt < retries:
                    sleep_time = delay * (2 ** (attempt - 1))  # 指数退避
                    logging.info(f"{sleep_time} 秒后重试...")
                    time.sleep(sleep_time)
                    # 停止页面加载并清理当前页面状态
                    try:
                        driver.execute_script("window.stop()")
                    except Exception as stop_exception:
                        logging.warning(f"执行 window.stop() 时发生异常: {stop_exception}")
                    driver.get("about:blank")
        return False  # 所有重试失败后返回 False


    def parse(self, response):

        url = response.url

        url_md5 = hashlib.md5(url.encode('utf-8')).hexdigest()
        # 判断是否已爬取
        if ListingHelper.exists_by_url_md5(url_md5):
            logger.warning("url:%s exists in db.", url_md5)
            return None

        data = {}
        success = False
        # user_id, driver = None, None  # 定义局部变量
        try:
            success = False
            # user_id, driver = self.driver_pool.get_driver()
            # self.close_other_tabs(driver)

            # 检查 WebDriver 是否有效
            if self.manager.is_driver_valid():
                # 进行你的操作，例如打开页面
                self.manager.driver.get(url)
            else:
                logger.warning("WebDriver 无效，尝试重新启动浏览器。")
                self.manager.restart_browser()
                self.manager.driver.get(url)

            # logger.debug("parse_listing user_id:%s, url:%s, data:%s", user_id, url, data)

            self.safe_get(self.manager.driver, url)
            # # 页面解析逻辑
            # WebDriverWait(driver, 10).until(
            #     EC.presence_of_element_located(
            #         (By.XPATH, '//div[@class="hero-thumbnails hero-poster__hero-thumbnails"]'))
            # )

            self.scroll_down_slowly(self.manager.driver)

            src = self.manager.driver.page_source

            # with open("p2.html", "w", encoding="utf-8") as f:
            #     f.write(src)

            sel = Selector(text=src)
            item = CombinedRealEstateItem()

            item['name'] = self.name
            item['listing_type'] = 'sale'
            item['url'] = url
            item['url_md5'] = hashlib.md5(url.encode('utf-8')).hexdigest()
            item['origin_images'] = []
            item['image_meta'] = {}

            # address info
            item = self.parse_address(sel, item)

            # basic info
            item = self.parse_primary_features(sel, item)

            # map info
            item = self.parse_coordinates(sel, item)

            # description info
            item = self.parse_description(sel, item)

            # features info
            item = self.parse_property_features(sel, item)

            item = self.parse_agent_and_agency(sel, item)

            item = self.parse_property_id_type(url, sel, item)

            item = self.parse_price(sel, item)

            logger.info(item)

            if not item.get('unique_id') or ListingHelper.exists_by_unique_id(item.get('unique_id')):
                raise ValueError('unique_id error or unique_id exists')

            # 使用 XPath 选择最后一个具有 role="button" 的子 div
            # last_button = sel.xpath('//div[@class="hero-thumbnails hero-poster__hero-thumbnails"]/div[@role="button"][last()]')

            self.manager.driver.execute_script(self.js_top)

            # 从第一张图片入手，获取总图片 并且点击它
            # 提取srcset中的图片链接
            first_srcset = sel.xpath('//div[@class="hero-image"]//source/@srcset').get()
            # 提取img标签中的src
            first_img_src = sel.xpath('//div[@class="hero-image"]//img/@src').get()
            # 提取alt属性中的总图片数
            alt_text = sel.xpath('//div[@class="hero-image"]//img/@alt').get()
            total_images = None
            if alt_text:
                match = re.search(r'image \d+ of (\d+)', alt_text)
                if match:
                    total_images = int(match.group(1))
                    logger.info("will be crawl total images:%s", total_images)

            # 点击展开gallery
            try:
                gallery_btn = WebDriverWait(self.manager.driver, 30).until(
                    EC.element_to_be_clickable(
                        (By.CLASS_NAME, 'hero-image'))
                )
                self.manager.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", gallery_btn)
                ActionChains(self.manager.driver).move_to_element(gallery_btn).click().perform()
                time.sleep(1)
            except Exception as e:
                logger.error(f"Error clicking button: {e}")

            # 等待页面加载
            wait = WebDriverWait(self.manager.driver, 10)
            wait.until(EC.presence_of_element_located((By.CLASS_NAME, "pswp__img")))

            # 初始化集合用于存储图片链接
            image_src_set = set()

            while True:
                # 记录当前集合大小
                previous_count = len(image_src_set)

                # 获取当前页面中所有图片的 src 属性
                # 使用 XPath 定位所有具有 pswp__img 类名的 <img> 标签
                img_elements = self.manager.driver.find_elements(By.XPATH, '//img[contains(@class, "pswp__img")]')
                for img in img_elements:
                    src = img.get_attribute("src")
                    if src:
                        logger.info("gallery add src: %s", src)
                        image_src_set.add(src)

                # 点击“下一张”按钮
                try:
                    next_button = wait.until(EC.element_to_be_clickable((By.CLASS_NAME, "pswp__button--arrow--right")))

                    self.manager.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", next_button)
                    ActionChains(self.manager.driver).move_to_element(next_button).click().perform()
                except Exception as e:
                    logger.error(f"无法点击“下一张”按钮：{e}")
                    break

                # 等待新图片加载
                time.sleep(1)

                # 获取更新后的集合大小
                current_count = len(image_src_set)

                # 判断集合是否增长
                if current_count == previous_count:
                    logger.info("未加载新图片，轮播结束:%s", current_count)
                    break

            # 输出所有收集到的图片链接
            logger.info("收集到的图片链接大小：%s", len(image_src_set))
            for src in image_src_set:
                logger.info(src)

            if total_images and total_images > len(image_src_set):
                logger.error("cannot load all images. %s > %s", total_images, len(image_src_set))
                raise ValueError("cannot load all images. %s > %s" % (total_images, len(image_src_set)))

            for src in image_src_set:
                item['origin_images'].append(src)
                item['image_meta'][src] = 'property'  # 房屋展示图


            # Floorplan
            try:
                # 等待“Floorplan”按钮可点击
                wait = WebDriverWait(self.manager.driver, 10)
                floorplan_button = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[@title='Floorplan']")))

                self.manager.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", floorplan_button)
                # 点击“Floorplan”按钮
                ActionChains(self.manager.driver).move_to_element(floorplan_button).click().perform()

                # floorplan_button.click()
                logger.info("成功点击了 'Floorplan' 按钮。")

                try:
                    # 等待图片元素加载完成
                    wait = WebDriverWait(self.manager.driver, 10)
                    floorplan_img = wait.until(EC.presence_of_element_located((By.XPATH, '//img[contains(@class, "pswp__img")]')))

                    # 获取图片的 src 属性
                    floorplan_src = floorplan_img.get_attribute("src")
                    logger.info(f"Floorplan 图片链接: {floorplan_src}")

                    # 这里要特殊处理一下：如果floorplan在orgin_images中则不需要插入了

                    item['origin_images'].append(floorplan_src)
                    item['image_meta'][floorplan_src] = 'floorplan'  # 户型图

                except Exception as e:
                    logger.error(f"获取Floorplan图片链接时发生错误: {e}")

            except Exception as e:
                logger.error(f"点击Floorplan按钮时发生错误: {e}")



            # try:
            #     next_btn = WebDriverWait(self.manager.driver, 30).until(
            #         EC.element_to_be_clickable(
            #             (By.XPATH, '//div[@data-testid="hero-thumbnails"]/div[@role="button"][last()]'))
            #     )
            #     self.manager.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", next_btn)
            #     ActionChains(self.manager.driver).move_to_element(next_btn).click().perform()
            #     time.sleep(1)
            #     self.manager.driver.execute_script(self.js)
            #
            #     imgs = self.manager.driver.find_elements(By.XPATH, "//img")
            #     for img in imgs:
            #         self.manager.driver.execute_script("arguments[0].scrollIntoView(true);", img)
            #         time.sleep(0.4)
            # except Exception as e:
            #     logger.error("发生了错误：%s", e)
            #     raise ValueError("发生了错误:%s", e)
            #
            # src = self.manager.driver.page_source
            #
            # with open("p3.html", "w", encoding="utf-8") as f:
            #     f.write(src)
            #
            # sel = Selector(text=src)
            #
            # try:
            #     item = self.parse_property_images(sel, item)
            # except ValueError as e:
            #     logger.error("parse images error:%s", e)
            #     raise ValueError("parse images error:%s", e)

            # item["image_urls"] = item["origin_images"]
            item = self.order_images(item)

            # process data
            # setdefault() 方法会在指定键不存在时，将其设置为给定的默认值。
            # for key, value in data.items():
            #     item.setdefault(key, value)

            logger.info(item)

            # 检查关键字段是否存在
            if not item.get('address') or not item.get('origin_images'):
                logger.warning(f"关键字段缺失，重新加入retry队列: {item['url']}")
                self.r.rpush(self.retry_redis_key, json.dumps({"url": item['url'], "meta": {}}))
                return None

            success = True
            yield item
        except Exception as e:
            self.logger.warning(f"return {response.url} to redis because [解析失败] {response.url}, 原因: {e}")
            self.r.rpush(self.retry_redis_key, json.dumps({"url": url, "meta": {}}))

            # 将失败的 URL 放回 Redis 队列
            store_data = {
                "url": response.url,
                "meta": {},
            }
            self.r.rpush(self.redis_key, json.dumps(store_data))

        # 即使在 try 或 except 中使用了 return 或 break，finally 都会被先执行再生效。
        # 如果 finally 中也有 return，它会覆盖前面的 return 值，需要特别小心。
        finally:
            # self.driver_pool.release_driver(user_id, driver)
            if success:
                logger.info('%s success processed.', url)

        return None

    def parse_property_id_type(self, url: str, sel: Selector, item: CombinedRealEstateItem) -> CombinedRealEstateItem:
        """
        从 URL 或 HTML 内容中提取 property ID 和 property type。

        异常:
            ValueError: 如果无法提取 property ID 或 property type。
        """

        # 尝试从 URL 中提取
        url_pattern = re.compile(r'/property-([a-zA-Z]+)-[a-zA-Z]+-[\w\+]+-(\d+)')
        match = url_pattern.search(url)
        if match:
            item['property_type'] = match.group(1).lower()
            item['unique_id'] = match.group(2)
            return item

        # 提取 Property ID
        id_elements = sel.xpath('//p[contains(text(), "Property ID")]/text()')
        if id_elements:
            id_text = ''.join(id_elements)
            id_match = re.search(r'Property ID:\s*(\d+)', id_text)
            if id_match:
                item['unique_id'] = id_match.group(1)
            else:
                raise ValueError("无法从 HTML 中提取 property ID。")
        else:
            raise ValueError("HTML 中未找到包含 'Property ID' 的段落。")

        # 提取 property_type
        property_type_text = sel.xpath("//ul[contains(@class, 'property-info__primary-features')]//p[last()]/text()").get()
        if property_type_text:
            item['property_type'] = property_type_text.strip().lower()

        return item

    def parse_price(self, sel: Selector, item: CombinedRealEstateItem) -> CombinedRealEstateItem:
        """
        从 Selector 中提取价格信息，并填充到 item 的字段中。
        """
        # 提取价格文本
        price_text = sel.xpath('//span[contains(@class, "property-price")]/text()').get(default='').strip()
        item['price_text'] = price_text

        # 提取价格 PDF 链接
        pdf_url = sel.xpath('//a[contains(@href, ".pdf")]/@href').get()
        if pdf_url:
            item['statement_pdf'] = pdf_url
            item['origin_pdfs'] = [pdf_url]

        # 初始化最低和最高价格
        lower_price = None
        upper_price = None

        # 处理价格范围，如 "$1,950,000 - $2,100,000"
        range_match = re.match(r'^\$?([\d,]+)\s*[-–—]\s*\$?([\d,]+)', price_text)
        if range_match:
            lower_price = int(range_match.group(1).replace(',', ''))
            upper_price = int(range_match.group(2).replace(',', ''))

        # 处理单一价格，如 "$250,000"
        elif re.match(r'^\$?[\d,]+$', price_text):
            lower_price = upper_price = int(price_text.replace('$', '').replace(',', ''))

        # 处理带有描述的价格，如 "OFFERS OVER $489,000"
        else:
            single_price_match = re.search(r'\$([\d,]+)', price_text)
            if single_price_match:
                lower_price = int(single_price_match.group(1).replace(',', ''))
                upper_price = None  # 无法确定上限

        # 填充到 item 中
        item['lower_price'] = lower_price
        item['upper_price'] = upper_price

        return item

    def parse_property_images(self, sel: Selector, item: CombinedRealEstateItem) -> CombinedRealEstateItem:
        # 定义允许的图片扩展名
        allowed_extensions = ('.jpg', '.jpeg', '.png', '.webp')

        # 提取房屋展示图片
        image_urls = sel.xpath('//button[contains(@class, "overview-MediaImage")]//picture//img/@src').getall()
        for url in image_urls:
            if url and url.lower().endswith(allowed_extensions):
                if 'placeholderSrc' in url:
                    raise ValueError(f"检测到占位符图片，终止解析。URL: {url}")
                item['origin_images'].append(url)
                item['image_meta'][url] = 'property'  # 房屋展示图
            else:
                logger.error("不支持的图片格式或无效链接：%s", url)

        # 提取户型图
        floorplan_urls = sel.xpath('//button[contains(@class, "overview-MediaFloorplan")]//picture//img/@src').getall()
        for url in floorplan_urls:
            if url and url.lower().endswith(allowed_extensions):
                if 'placeholderSrc' in url:
                    raise ValueError(f"检测到占位符图片，终止解析。URL: {url}")
                item['origin_images'].append(url)
                item['image_meta'][url] = 'floorplan'  # 户型图
            else:
                logger.error("不支持的图片格式或无效链接：%s", url)

        return item


    def order_images(self, item: CombinedRealEstateItem) -> CombinedRealEstateItem:
        # 分类图片并记录索引
        # item['origin_images'] = [
        #     'https://example.com/images/property1.jpg',
        #     'https://example.com/images/floorplan1.jpg',
        #     'https://example.com/images/agent1.jpg',
        #     'https://example.com/images/property2.jpg',
        #     'https://example.com/images/floorplan2.jpg'
        # ]
        # item['image_meta'] = {
        #     'https://example.com/images/property1.jpg': 'property',
        #     'https://example.com/images/floorplan1.jpg': 'floorplan',
        #     'https://example.com/images/agent1.jpg': 'agent',
        #     'https://example.com/images/property2.jpg': 'property',
        #     'https://example.com/images/floorplan2.jpg': 'floorplan'
        # }
        # item['image_type_groups']：一个字典，键为图片类型，值为该类型下的图片 URL 列表。例如：
        # {
        #     'property': ['url1', 'url3'],
        #     'floorplan': ['url2'],
        #     'agent': ['url4']
        # }
        # item['image_index_in_type']：一个字典，键为图片 URL，值为该图片在其类型列表中的索引。例如：
        # {
        #     'url1': 0,
        #     'url2': 0,
        #     'url3': 1,
        #     'url4': 0
        # }
        # 通过以上处理，您可以方便地根据图片类型对图片进行分类，并获取每个图片在其分类中的索引，便于后续的处理和命名。
        image_type_groups = {}
        image_index_in_type = {}
        for url in item['origin_images']:
            image_type = item['image_meta'].get(url, 'property')  # 默认类型为 'property'
            if image_type not in image_type_groups:
                image_type_groups[image_type] = []
            index = len(image_type_groups[image_type])
            image_type_groups[image_type].append(url)
            image_index_in_type[url] = index

        item['image_type_groups'] = image_type_groups
        item['image_index_in_type'] = image_index_in_type

        return item

    def parse_address(self, sel: Selector, item: CombinedRealEstateItem) -> CombinedRealEstateItem:
        """
        从 Selector 中提取地址信息，并填充到 item 的字段中。
        """
        # 提取完整地址
        full_address = sel.xpath('//h1[contains(@class, "property-info-address")]/text()').get(default='').strip()
        item['address'] = full_address
        item['title'] = full_address

        # 使用正则表达式提取街道、郊区、州和邮政编码
        # 示例地址格式：'9 Willurah Street, Forest Hill, Vic 3131'
        address_pattern = re.compile(
            r'^(?P<street>.*?),\s*(?P<suburb>.*?),\s*(?P<state>[A-Za-z]{2,3})\s+(?P<postcode>\d{4})$'
        )

        match = address_pattern.match(full_address)
        if match:
            item['street'] = match.group('street').strip()
            item['suburb'] = match.group('suburb').strip()
            item['state'] = match.group('state').strip().upper()
            item['postcode'] = match.group('postcode').strip()
        else:
            # 如果地址格式不符合预期，可以设置为 None 或留空
            item['street'] = None
            item['suburb'] = None
            item['state'] = None
            item['postcode'] = None

        return item

    def parse_primary_features(self, sel: Selector, item: CombinedRealEstateItem) -> CombinedRealEstateItem:
        features = sel.xpath('//ul[contains(@class, "property-info__primary-features")]//li')
        for feature in features:
            aria_label = feature.xpath('./@aria-label').get()
            value = feature.xpath('.//p/text()').get()
            if aria_label and value:
                aria_label = aria_label.lower()
                if 'bedroom' in aria_label:
                    item['bedrooms'] = int(value)
                elif 'bathroom' in aria_label:
                    item['bathrooms'] = int(value)
                elif 'car space' in aria_label:
                    item['car_spaces'] = int(value)
                elif 'land size' in aria_label:
                    # 匹配前导数字，可能带小数或单位，如 "585m²"、"828.5 sqm"
                    match = re.search(r'([\d.]+)', value)
                    if match:
                        land_size = match.group(1)
                        try:
                            item['land_size'] = int(float(land_size))
                        except ValueError:
                            logger.warning("parse error land_size:%s", value)
        return item

    def parse_property_features(self, sel: Selector, item: CombinedRealEstateItem) -> CombinedRealEstateItem:
        """
        解析房产特征信息，并将其存储为 JSON 格式。

        参数:
            sel (Selector): Scrapy 的 Selector 对象。
            item (CombinedRealEstateItem): 用于存储提取数据的 Item 对象。

        返回:
            CombinedRealEstateItem: 包含提取特征信息的 Item 对象。
        """
        features = {}

        # 提取所有的 <p> 标签文本
        p_elements = sel.xpath('//div[@data-testid="all-property-features-section"]//p/text()').getall()

        for text in p_elements:
            text = text.strip()
            if not text:
                continue
            if ':' in text:
                key, value = text.split(':', 1)
                # key = key.lower().replace(' ', '_')  # 转小写+下划线格式 TODO: "Air conditioning" → {'air_conditioning': True}
                features[key.strip()] = value.strip()
            else:
                features[text] = True

        logger.info("features: %s", features)
        # 将特征字典转换为 JSON 字符串，并存储到 item 中, 如果多次转码会有反斜杠在逗号前面，这样php在读取时会出问题
        # item['features'] = json.dumps(features, ensure_ascii=False)
        item['features'] = features
        logger.info("item['features']: %s", item['features'])

        return item

    def parse_coordinates(self, sel: Selector, item: CombinedRealEstateItem) -> CombinedRealEstateItem:
        """
        从静态地图的背景图像 URL 中提取经纬度坐标，并添加到 item 中。

        :param sel: Scrapy 的 Selector 对象，用于解析 HTML。
        :param item: 包含爬取数据的字典。
        :return: 更新后的 item，包含 'latitude' 和 'longitude' 键（如果提取成功）。
        """
        # 提取 style 属性中的背景图像 URL
        style_attr = sel.xpath('//div[contains(@class, "static-map__img")]/@style').get()
        if style_attr:
            # 使用正则表达式提取 URL
            match = re.search(r'url\(["\']?(.*?)["\']?\)', style_attr)
            if match:
                map_url = match.group(1)
                # 提取 URL 中的经纬度参数
                from urllib.parse import unquote
                decoded_url = unquote(map_url)

                # 使用正则表达式提取经纬度
                coord_match = re.search(r'markers=.*?\|(-?\d+\.\d+),(-?\d+\.\d+)', decoded_url)
                if coord_match:
                    latitude = float(coord_match.group(1))
                    longitude = float(coord_match.group(2))
                    item['latitude'] = latitude
                    item['longitude'] = longitude
        return item

    def parse_description(self, sel: Selector, item: CombinedRealEstateItem) -> CombinedRealEstateItem:
        """
        提取房产描述信息并填充到 item 中。

        参数:
            sel (Selector): Scrapy 的 Selector 对象。
            item (dict): 存储提取信息的字典。

        返回:
            dict: 包含描述信息的 item。
        """
        # 提取描述标题
        description_title = sel.xpath('//div[@data-testid="PropertyDescription"]//h2/text()').get()
        if description_title:
            item['description_title'] = description_title.strip()

        # 提取完整描述内容
        description_paragraphs = sel.xpath('//div[@data-testid="PropertyDescription"]//p[@class="property-description__content"]/text()').getall()
        if description_paragraphs:
            # 合并段落并去除多余空白
            # full_description = '\n'.join([para.strip() for para in description_paragraphs if para.strip()])
            # item['description'] = full_description
            item['description'] = description_paragraphs[0].strip()
        return item

    def parse_agent_and_agency(self, sel: Selector, item: CombinedRealEstateItem) -> CombinedRealEstateItem:
        """
        解析代理人和公司信息，并将其存储为 JSON 格式。

        参数:
            sel (Selector): Scrapy 的 Selector 对象。
            item (CombinedRealEstateItem): 用于存储提取数据的 Item 对象。

        返回:
            CombinedRealEstateItem: 包含提取代理人和公司信息的 Item 对象。
        """
        agents = []

        # 提取代理人信息
        agent_elements = sel.xpath('//div[@class="contact-agent-panel"]//li[contains(@class, "agent-info__agent")]')
        for agent in agent_elements:
            name = agent.xpath('.//a[contains(@class, "agent-info__name")]/text()').get(default='').strip()
            # profile_url = agent.xpath('.//a[contains(@class, "agent-info__name")]/@href').get(default='').strip()
            photo_url = agent.xpath('.//div[contains(@class, "agent-info__photo")]//img/@src').get(default='').strip()
            # added to origin_images to download
            if photo_url:
                item['origin_images'].append(photo_url)
                item['image_meta'][photo_url] = 'agent'  # 代理人头像
            # rating = agent.xpath('.//span[contains(@class, "styles__AvgRatingText")]/text()').get(default='').strip()
            # reviews = agent.xpath('.//span[contains(@class, "styles__ReviewsText")]/text()').get(default='').strip()
            phone = agent.xpath('.//div[contains(@class, "phone")]//a[contains(@href, "tel:")]/@href').re_first( r'tel:(\d+)', default='').strip()

            agent_info = {
                'name': name,
                # 'profile_url': profile_url,
                'photo_url': photo_url,
                # 'rating': rating,
                # 'reviews': reviews,
                'phone': phone
            }
            agents.append(agent_info)

        # 提取公司信息
        agency_name = sel.xpath( '//div[contains(@class, "sidebar-traffic-driver")]//a[contains(@class, "sidebar-traffic-driver__name")]/text()').get( default='').strip()
        # agency_url = sel.xpath(
        #     '//div[contains(@class, "sidebar-traffic-driver")]//a[contains(@class, "sidebar-traffic-driver__name")]/@href').get(
        #     default='').strip()
        agency_address = sel.xpath('//div[contains(@class, "sidebar-traffic-driver__detail-info")]/text()').get( default='').strip()
        agency_url = sel.xpath('//img[@class="branding__image"]/@src').get()
        if agency_url:
            item['origin_images'].append(agency_url)
            item['image_meta'][agency_url] = 'agency'  # 代理人头像

        agency = {
            'name': agency_name,
            'agency_url': agency_url,
            'address': agency_address
        }

        # 将代理人和公司信息存储到 item 中
        # item['agents'] = json.dumps(agents, ensure_ascii=False)
        # item['agency'] = json.dumps(agency, ensure_ascii=False)
        item['agents'] = agents
        item['agency'] = agency
        logger.info("item['agents']:%s", item['agents'])
        logger.info("item['agency']:%s", item['agency'])

        return item

    def scroll_down_slowly(self, driver: webdriver, pause_time=0.5, scroll_increment=100):
        """
        缓慢地向下滚动页面，直到页面底部。

        :param driver: Selenium WebDriver 实例
        :param pause_time: 每次滚动后的暂停时间（秒）
        :param scroll_increment: 每次滚动的像素数
        """
        last_height = driver.execute_script("return document.body.scrollHeight")
        current_position = 0

        while current_position < last_height:
            current_position += scroll_increment
            driver.execute_script(f"window.scrollTo(0, {current_position});")
            time.sleep(pause_time)
            last_height = driver.execute_script("return document.body.scrollHeight")

    # —— 1. 在类中定义 normalize_url 方法 —— #
    def normalize_url(self, path: str) -> str:
        """
        如果 path 是相对路径，则拼接上 BASE_DOMAIN；否则直接返回绝对 URL。
        """
        return urljoin(self.BASE_DOMAIN, path)

    # —— 2. 在类中定义 parse_property_url 方法 —— #
    def parse_property_url(self, path: str) -> dict:
        """
        从单一路径（如 "/property-house-wa-dunsborough-147818112"）中提取：
          - property_type: 房屋类型
          - state: 州简称
          - region: 地区
          - id: 房屋 ID
        """
        clean = path.lstrip('/')
        pattern = re.compile(r'^property-([^-]+)-([^-]+)-([^-]+)-(\d+)$')
        m = pattern.match(clean)
        if not m:
            raise ValueError(f"无法解析路径: {path}")
        prop_type, state, region, prop_id = m.groups()
        return {
            "property_type": prop_type,
            "state": state,
            "region": region,
            "id": prop_id,
        }

    def extract_property_id(self, url):
        """
        判断给定的 URL 是否为 realestate.com.au 的房产详情页，并提取房产 ID。

        参数:
            url (str): 要检查的 URL。

        返回:
            str 或 None: 如果是房产详情页，返回房产 ID；否则返回 None。
        """
        pattern = r'^https?://(?:www\.)?realestate\.com\.au/property-[\w-]+-(\d+)$'
        match = re.match(pattern, url)
        if match:
            return match.group(1)
        return None

    def ensure_connection(self):
        try:
            self.r.ping()
            logger.info("Redis 连接成功。")
        except ConnectionError:
            logger.warning("Redis 连接断开，正在尝试重新连接...")
            self.r = get_redis_client()
            # 可选：再次验证连接
            try:
                self.r.ping()
                logger.info("Redis 重新连接成功。")
            except ConnectionError:
                logger.error("Redis 重新连接失败。")
                raise

    def close_other_tabs(self, driver):
        try:
            current = driver.current_window_handle
            for handle in driver.window_handles:
                if handle != current:
                    driver.switch_to.window(handle)
                    driver.close()
            driver.switch_to.window(current)
        except Exception as e:
            self.logger.warning(f"[Selenium] 无法关闭标签页: {e}")
