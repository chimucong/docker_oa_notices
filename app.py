import os
import time
import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from logging.handlers import TimedRotatingFileHandler

from flask import Flask, Response, request
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.service import Service


# ===== 自定义 Formatter，使用北京时间（UTC+8），日期格式精确到毫秒 =====
class BeijingTimeFormatter(logging.Formatter):
    BJ_TZ = timezone(timedelta(hours=8))

    def converter(self, timestamp):
        dt = datetime.fromtimestamp(timestamp, self.BJ_TZ)
        return dt.timetuple()

    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, self.BJ_TZ)
        if datefmt:
            s = dt.strftime(datefmt)
        else:
            s = dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        return s


formatter = BeijingTimeFormatter(
    fmt='[%(asctime)s] [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S.%f'
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

log_dir = "log"
os.makedirs(log_dir, exist_ok=True)  # 确保 log 目录存在
log_file_path = os.environ.get(
    "LOG_PATH", os.path.join(log_dir, "oa_notices.log"))

file_handler = TimedRotatingFileHandler(
    log_file_path,
    when="midnight",
    interval=1,
    backupCount=7,
    encoding="utf-8",
    utc=False
)
file_handler.suffix = "%Y-%m-%d.log"
file_handler.converter = lambda *args: datetime.now(
    timezone(timedelta(hours=8))).timetuple()
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# ========= 环境变量检查 =========
debug_mode = os.environ.get("DEBUG", "0") == "1"
logger.info(f"debug_mode: {debug_mode}")
username = os.environ.get("OA_USERNAME")
password = os.environ.get("OA_PASSWORD")
if not username or not password:
    logger.error("缺少环境变量：OA_USERNAME 或 OA_PASSWORD")
    os._exit(1)

app = Flask(__name__)
flask_logger = logging.getLogger('werkzeug')
flask_logger.setLevel(logging.DEBUG)
flask_logger.handlers = []  # 清除默认 handler
flask_logger.addHandler(console_handler)
flask_logger.addHandler(file_handler)

# ========= 缓存和锁 =========
notices_cache = None
cache_last_updated = None
CACHE_TTL = timedelta(minutes=30)

cache_condition = threading.Condition()
fetching_in_progress = False


def wait(driver, id_name, by=By.ID):
    WebDriverWait(driver, 60).until(
        EC.presence_of_element_located((by, id_name)))


def switch_to_last_tab(driver, wait_title=True):
    driver.switch_to.window(driver.window_handles[-1])
    if wait_title:
        try:
            WebDriverWait(driver, 20).until(lambda d: d.title != "")
        except TimeoutException:
            pass


def fetch_notices_from_oa():
    global notices_cache, cache_last_updated
    logger.info("开始抓取 OA 公告...")

    options = webdriver.ChromeOptions()
    options.binary_location = '/app/chrome-linux64/chrome'
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,800")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--allow-insecure-localhost")
    options.add_argument(
        "--unsafely-treat-insecure-origin-as-secure=http://oa.wzvtc.cn")

    if debug_mode:
        options.add_argument("--remote-debugging-port=9222")
        options.add_argument("--remote-debugging-address=0.0.0.0")
        options.add_argument("--remote-allow-origins=*")

    logger.info('加载 driver ...')
    service = Service(executable_path='/app/chromedriver-linux64/chromedriver')
    driver = webdriver.Chrome(service=service, options=options)
    logger.info('加载 driver 完毕')
    logger.info(f"ChromeDriver 路径: {driver.service.path}")

    try:
        url = "https://id.wzvtc.cn/"
        logger.info(f'打开: {url}')
        driver.get(url)
        wait(driver, "username")
        logger.info(f'加载完毕: {url}')

        driver.find_element(By.ID, "username").send_keys(username)
        driver.find_element(By.ID, "password").send_keys(password)
        driver.find_element(By.ID, "password").send_keys(Keys.RETURN)

        elem = WebDriverWait(driver, 60).until(
            EC.any_of(
                EC.presence_of_element_located(
                    (By.XPATH, "//a[.//nobr[text()='温职院OA系统']]")),
                EC.presence_of_element_located((By.ID, "msg"))
            )
        )
        if '认证失败' in elem.text:
            logger.error(elem.text)
            os._exit(1)

        logger.info('登录账号成功')

        url = "http://oa.wzvtc.cn/login/Login.jsp"
        logger.info(f"加载: {url}")
        driver.get(url)
        wait(driver, "mainFrame")
        driver.switch_to.frame("mainFrame")
        wait(driver, "more_340")
        logger.info("加载完毕")

        logger.info('开始读取公告页面')
        driver.find_element(By.ID, "more_340").click()

        switch_to_last_tab(driver, False)
        wait(driver, 'tabcontentframe')
        time.sleep(10)

        notices = driver.execute_script("""
            const iframe = document.getElementById('tabcontentframe');
            const iframeDoc = iframe.contentWindow.document;
            const tbody = iframeDoc.getElementById('_xTable');
            if (!tbody) return [];

            const notices = [];
            let idx = 0;
            const rowsSnapshot = document.evaluate(
                ".//tr[contains(@class, 'e8_table_new')]",
                tbody,
                null,
                XPathResult.ORDERED_NODE_SNAPSHOT_TYPE,
                null
            );

            for (let i = 0; i < rowsSnapshot.snapshotLength; i++) {
                const row = rowsSnapshot.snapshotItem(i);
                const cells = row.getElementsByTagName('td');
                if (cells.length >= 7) {
                    const title = cells[2].innerText.trim();
                    const linkEl = cells[2].querySelector('a');
                    const link = linkEl ? linkEl.href : '';
                    const publisher = cells[3].innerText.trim();
                    const pub_date = cells[4].innerText.trim();
                    const category = cells[6].innerText.trim();

                    notices.push({ title, link, publisher, pub_date, category, idx });
                    idx++;
                }
            }
            return notices;
        """)

        with cache_condition:
            notices_cache = notices
            cache_last_updated = datetime.now(timezone(timedelta(hours=8)))
            logger.info("公告抓取完成，共 %d 条", len(notices))
            return notices
    except Exception as e:
        logger.error("公告抓取出错: %s", str(e))
        return None
    finally:
        driver.quit()


def maybe_refresh_cache():
    global fetching_in_progress
    refresh_hours = {9, 12, 15, 18}
    triggered = set()

    while True:
        now = datetime.now(timezone(timedelta(hours=8)))
        if now.hour in refresh_hours and 0 <= now.minute <= 2:
            if now.hour not in triggered:
                with cache_condition:
                    if not fetching_in_progress:
                        fetching_in_progress = True
                        logger.info(
                            f"[定时刷新] 当前时间: {now.strftime('%Y-%m-%d %H:%M:%S')}，开始刷新缓存")
                        try:
                            fetch_notices_from_oa()
                        finally:
                            fetching_in_progress = False
                            cache_condition.notify_all()
                triggered.add(now.hour)

        for h in list(triggered):
            if now.hour != h or now.minute > 5:
                triggered.remove(h)

        time.sleep(30)


@app.route("/notices")
def get_notices():
    global fetching_in_progress
    with cache_condition:
        if not notices_cache and not fetching_in_progress:
            fetching_in_progress = True
            threading.Thread(target=fetch_and_release).start()

        if not notices_cache:
            return Response(
                json.dumps({"error": "公告未获取，请稍后再试"}, ensure_ascii=False),
                content_type="application/json; charset=utf-8",
                status=500
            )

        return Response(
            json.dumps({'last_updated_time': cache_last_updated.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S"),
                        'data': notices_cache}, ensure_ascii=False),
            content_type="application/json; charset=utf-8"
        )


def fetch_and_release():
    global fetching_in_progress
    try:
        fetch_notices_from_oa()
    finally:
        with cache_condition:
            fetching_in_progress = False
            cache_condition.notify_all()


@app.route("/refresh_notices")
def force_refresh():
    global fetching_in_progress
    with cache_condition:
        if fetching_in_progress:
            return Response(
                json.dumps({"message": "已有线程正在刷新缓存"}, ensure_ascii=False),
                content_type="application/json; charset=utf-8"
            )
        fetching_in_progress = True

    def worker():
        global fetching_in_progress
        try:
            fetch_notices_from_oa()
        finally:
            with cache_condition:
                fetching_in_progress = False
                cache_condition.notify_all()

    threading.Thread(target=worker).start()

    return Response(
        json.dumps({"message": "已启动强制刷新"}, ensure_ascii=False),
        content_type="application/json; charset=utf-8"
    )


if __name__ == "__main__":
    fetch_notices_from_oa()
    threading.Thread(target=maybe_refresh_cache, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, debug=debug_mode)
