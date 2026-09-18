# -*- coding: utf-8 -*-
"""
黄金实时行情 + 日K线 本地数据服务

只用 Python 标准库，无需 pip 安装任何依赖。
启动：  python gold_server.py
然后浏览器打开： http://127.0.0.1:8765

提供接口：
  GET /api/quote         实时报价（国际金 XAU/USD、美元人民币汇率、上海金 Au(T+D)）
  GET /api/kline?days=N  日K线历史（数据源含当日）
"""

import datetime
import gzip
import json
import os
import re
import socket
import ssl
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 可通过环境变量覆盖（容器部署时用 GOLD_HOST=:: 开启 IPv4/IPv6 双栈）
HOST = os.environ.get("GOLD_HOST", "127.0.0.1")
PORT = int(os.environ.get("GOLD_PORT", "8765"))
TZNAME = os.environ.get("TZ", "Asia/Shanghai")
ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "gold")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
SINA_REF = "https://finance.sina.com.cn"
OZ_TO_GRAM = 31.1034768

SINA_QUOTE_URL = "https://hq.sinajs.cn/list=hf_XAU"           # 伦敦金现货（美元/盎司，需 Referer）
SINA_FX_URL = "https://hq.sinajs.cn/list=USDCNY"              # 美元人民币
SINA_AUTD_URL = "https://hq.sinajs.cn/list=SGE_AUTD"          # 上海金 Au(T+D)（人民币/克）
SINA_KLINE_URL = ("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_t/"
                  "GlobalFuturesService.getGlobalFuturesDailyKLine?symbol=XAU")
GOLDAPI_URL = "https://api.gold-api.com/price/XAU"
ERAPI_URL = "https://open.er-api.com/v6/latest/USD"

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

_cache = {"quote": {"at": 0, "data": None}, "kline": {"at": 0, "data": None}}
_cache_lock = threading.Lock()
START_AT = time.time()


# --------------------------------------------------------------------------- #
#  基础抓取
# --------------------------------------------------------------------------- #
def http_get(url, referer=None, timeout=10, charset="utf-8"):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Connection": "close",
    })
    if referer:
        req.add_header("Referer", referer)
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding", "").lower().find("gzip") >= 0:
            raw = gzip.decompress(raw)
    return raw.decode(charset, "replace")


def now_str():
    """本地时间字符串。容器里优先用 zoneinfo（需 tzdata），取不到则回退系统本地时区。"""
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo(TZNAME)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def f(v, default=None):
    try:
        x = float(v)
        return x if x != 0 or default is None else default
    except Exception:
        return default


# --------------------------------------------------------------------------- #
#  各数据源
# --------------------------------------------------------------------------- #
def src_sina_xau():
    """新浪伦敦金现货：现价 + 当日开高低 + 昨收（美元/盎司）"""
    txt = http_get(SINA_QUOTE_URL, SINA_REF, timeout=8, charset="gbk")
    m = re.search(r'var hq_str_hf_XAU="([^"]*)"', txt)
    if not m or not m.group(1).strip():
        return None
    p = m.group(1).split(",")
    if len(p) < 13:
        return None
    return {
        "source": "sina",
        "price": f(p[0]), "bid": f(p[2]), "ask": f(p[3]),
        "high": f(p[4]), "low": f(p[5]),
        "prev_close": f(p[7]), "open": f(p[8]),
        "time": p[6], "date": p[12],
    }


def src_goldapi_xau():
    """gold-api.com：XAU/USD 现货（CORS 友好，用作备用/交叉校验）"""
    j = json.loads(http_get(GOLDAPI_URL, timeout=8))
    return {
        "source": "gold-api",
        "price": f(j.get("price")),
        "updated_at": j.get("updatedAt"),
    }


def src_sina_fx():
    """美元人民币汇率"""
    txt = http_get(SINA_FX_URL, SINA_REF, timeout=8, charset="gbk")
    m = re.search(r'var hq_str_USDCNY="([^"]*)"', txt)
    if not m or not m.group(1).strip():
        return None
    p = m.group(1).split(",")
    bid, ask = f(p[1]), f(p[2])
    return {
        "source": "sina",
        "rate": round((bid + ask) / 2, 6) if bid and ask else bid,
        "bid": bid, "ask": ask,
        "time": p[0], "date": p[10] if len(p) > 10 else "",
    }


def src_erapi_fx():
    j = json.loads(http_get(ERAPI_URL, timeout=10))
    return {
        "source": "open.er-api",
        "rate": f((j.get("rates") or {}).get("CNY")),
        "updated_at": j.get("time_last_update_utc"),
    }


def src_sina_autd():
    """上海黄金交易所 Au(T+D)，人民币/克"""
    txt = http_get(SINA_AUTD_URL, SINA_REF, timeout=8, charset="gbk")
    m = re.search(r'var hq_str_SGE_AUTD="([^"]*)"', txt)
    if not m or not m.group(1).strip():
        return None
    p = m.group(1).split(",")
    if len(p) < 18:
        return None
    prev = f(p[5])
    last = f(p[3])
    pct = None
    if last and prev:
        pct = round((last - prev) / prev * 100, 4)
    return {
        "source": "sge-sina",
        "name": p[1],
        "price": last, "prev_close": prev, "open": f(p[6]),
        "high": f(p[7]), "low": f(p[8]),
        "change_pct": pct,
        "time": p[16],
    }


def fetch_kline(days=0):
    """新浪全球期货日K（伦敦金现货），days=0 表示全部历史"""
    txt = http_get(SINA_KLINE_URL, timeout=25, charset="utf-8")
    i = txt.find("(")
    j = txt.rfind(")")
    if i < 0 or j <= i:
        raise ValueError("日K接口返回格式异常")
    arr = json.loads(txt[i + 1:j])
    rows = []
    for it in arr:
        d = {"date": it.get("date"), "open": f(it.get("open")), "high": f(it.get("high")),
             "low": f(it.get("low")), "close": f(it.get("close"))}
        if d["date"] and None not in (d["open"], d["high"], d["low"], d["close"]):
            rows.append(d)
    if days and days > 0:
        rows = rows[-days:]
    return rows


# --------------------------------------------------------------------------- #
#  组装报价
# --------------------------------------------------------------------------- #
def build_quote():
    now = int(time.time())
    with _cache_lock:
        if _cache["quote"]["data"] and now - _cache["quote"]["at"] < 3:
            return _cache["quote"]["data"]

    errors = {}
    xau_realtime = None
    xau_day = None
    fx = None
    autd = None

    def safe(tag, fn):
        try:
            return fn()
        except Exception as e:                       # 单源失败不影响整体
            errors[tag] = "%s: %s" % (type(e).__name__, str(e)[:120])
            return None

    with ThreadPoolExecutor(max_workers=5) as ex:
        f_sina = ex.submit(safe, "sina_xau", src_sina_xau)
        f_api = ex.submit(safe, "goldapi", src_goldapi_xau)
        f_fx = ex.submit(safe, "sina_fx", src_sina_fx)
        f_fx2 = ex.submit(safe, "erapi_fx", src_erapi_fx)
        f_autd = ex.submit(safe, "autd", src_sina_autd)
        xau_day = f_sina.result()
        xau_realtime = f_api.result()
        fx = f_fx.result()
        if not fx:
            fx = f_fx2.result()
        autd = f_autd.result()

    # 现价：新浪优先（含当日 OHLC），gold-api 兜底
    price = None
    if xau_day and xau_day.get("price"):
        price = xau_day["price"]
    elif xau_realtime and xau_realtime.get("price"):
        price = xau_realtime["price"]

    if not price:
        raise RuntimeError("所有国际金报价源均不可用")

    day = dict(xau_day or {})
    if day.get("price") is None:
        day["price"] = price
    # gold-api 只是价格源时，用它刷新现价（更新更快）
    if xau_realtime and xau_realtime.get("price"):
        day["price"] = xau_realtime["price"]
        day["price_source"] = "gold-api"
    else:
        day["price_source"] = day.get("source", "")

    prev = day.get("prev_close")
    chg = None
    pct = None
    if prev:
        chg = round(price - prev, 4)
        pct = round((price - prev) / prev * 100, 4)
    hi = day.get("high")
    lo = day.get("low")
    if hi is not None and price > hi:
        hi = price
    if lo is not None and (price < lo or lo == 0):
        lo = price
    if not day.get("open"):
        day["open"] = prev

    rate = fx.get("rate") if fx else None
    cny_g = round(price / OZ_TO_GRAM * rate, 4) if (price and rate) else None

    data = {
        "ok": True,
        "ts": now,
        "server_time": now_str(),
        "xau": {
            "price": price,
            "prev_close": prev,
            "open": day.get("open"),
            "high": hi, "low": lo,
            "change": chg, "change_pct": pct,
            "quote_time": day.get("time") or "",
            "quote_date": day.get("date") or "",
            "price_source": day.get("price_source") or "",
        },
        "fx": {
            "usdcny": rate,
            "bid": fx.get("bid") if fx else None,
            "ask": fx.get("ask") if fx else None,
            "source": fx.get("source") if fx else None,
            "time": (fx.get("time") if fx else None) or (fx.get("updated_at") if fx else None),
        },
        "autd": autd,
        "derived": {
            "cny_per_gram": cny_g,
            "usd_per_gram": round(price / OZ_TO_GRAM, 4) if price else None,
        },
        "errors": errors,
        "oz_to_gram": OZ_TO_GRAM,
    }

    with _cache_lock:
        _cache["quote"] = {"at": now, "data": data}
    return data


def kline_cached(days):
    now = int(time.time())
    with _cache_lock:
        if _cache["kline"]["data"] and now - _cache["kline"]["at"] < 300:
            rows = _cache["kline"]["data"]
        else:
            rows = None
    if rows is None:
        rows = fetch_kline(0)
        with _cache_lock:
            _cache["kline"] = {"at": now, "data": rows}
    return rows[-days:] if days and days > 0 else rows


# --------------------------------------------------------------------------- #
#  HTTP
# --------------------------------------------------------------------------- #
MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".svg": "image/svg+xml",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "GoldQuote/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def do_GET(self):
        path = self.path.split("?")[0]
        qs = {}
        if "?" in self.path:
            for kv in self.path.split("?", 1)[1].split("&"):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    qs[k] = urllib.request.unquote(v)

        try:
            if path in ("/", "/index.html"):
                self._file(os.path.join(WEB, "index.html"), "text/html; charset=utf-8")
            elif path.startswith("/api/ping"):
                # 轻量探活：不触发任何外部请求，供 Docker HEALTHCHECK / 负载均衡使用
                self._send(200, json.dumps({
                    "ok": True, "pong": True, "time": now_str(), "tz": TZNAME,
                    "uptime": int(time.time() - START_AT),
                }, ensure_ascii=False))
            elif path.startswith("/api/quote"):
                self._send(200, json.dumps(build_quote(), ensure_ascii=False))
            elif path.startswith("/api/kline"):
                days = int(qs.get("days", "0") or 0)
                rows = kline_cached(days)
                body = json.dumps({
                    "ok": True, "count": len(rows),
                    "updated": now_str(),
                    "data": rows,
                }, ensure_ascii=False)
                self._send(200, body)
            elif path.startswith("/api/"):
                self._send(404, json.dumps({"ok": False, "error": "unknown api"}))
            else:
                rel = path.lstrip("/")
                fp = os.path.normpath(os.path.join(WEB, rel))
                if fp.startswith(WEB) and os.path.isfile(fp):
                    ext = os.path.splitext(fp)[1].lower()
                    self._file(fp, MIME.get(ext, "application/octet-stream"))
                else:
                    self._send(404, "not found", "text/plain; charset=utf-8")
        except Exception as e:
            self._send(500, json.dumps({"ok": False, "error": "%s: %s" % (type(e).__name__, e)},
                                       ensure_ascii=False))

    def _file(self, fp, ctype):
        if not os.path.isfile(fp):
            self._send(404, "not found", "text/plain; charset=utf-8")
            return
        with open(fp, "rb") as fh:
            body = fh.read()
        self._send(200, body, ctype)


class DualStackServer(ThreadingHTTPServer):
    """IPv6 socket + IPV6_V6ONLY=0：同一个 socket 同时接受 IPv6 与 IPv4 连接。"""
    address_family = socket.AF_INET6

    def server_bind(self):
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except Exception:
            pass                                   # 某些平台不支持该选项，退化为纯 IPv6
        super().server_bind()


def make_server(host, port):
    """按 host 形态选 server 类型；双栈不可用时自动降级。返回 (server, 实际 host, 模式说明)"""
    host = host.strip()
    if host.startswith("[") and host.endswith("]"):     # 允许写成 [::]
        host = host[1:-1]

    want_v6 = (":" in host) or host == "::"
    if want_v6:
        if socket.has_dualstack_ipv6():
            try:
                return DualStackServer((host, port), Handler), host, "IPv6 双栈（同时接受 IPv4）"
            except OSError as e:
                print("!! IPv6 监听失败（%s），自动降级为 IPv4" % e)
        else:
            print("!! 当前系统不支持 IPv6 双栈，自动降级为 IPv4")
        return ThreadingHTTPServer(("0.0.0.0", port), Handler), "0.0.0.0", "IPv4（降级）"

    return ThreadingHTTPServer((host, port), Handler), host, "IPv4"


def local_addresses(port):
    """枚举本机可用于访问的地址，IPv6 单独返回以便拼成 http://[addr]:port 形式"""
    v4, v6 = [], []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), port):
            if len(info[4]) < 1:
                continue
            ip = info[4][0]
            if info[0] == socket.AF_INET:
                if not ip.startswith("127."):
                    v4.append(ip)
            elif info[0] == socket.AF_INET6:
                low = ip.lower()
                if low != "::1" and not low.startswith("fe80") and "%" not in ip:
                    v6.append(ip)
    except Exception:
        pass
    # 补一个默认出口探测，gethostname 拿不到 IPv6 时也能给出结果
    if not v6:
        try:
            s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
            s.connect(("2400:3200::1", 80))             # 阿里 DNS，仅用于取源地址，不发包
            v6.append(s.getsockname()[0])
            s.close()
        except Exception:
            pass
    return sorted(set(v4)), sorted(set(v6))


def main():
    argv = sys.argv[1:]
    open_browser = "--no-browser" not in argv and not os.environ.get("GOLD_NO_BROWSER")

    if not os.path.isfile(os.path.join(WEB, "index.html")):
        print("!! 缺少页面文件：%s" % os.path.join(WEB, "index.html"))
        return 1
    srv, real_host, mode = make_server(HOST, PORT)
    srv.daemon_threads = True
    actual_port = srv.server_address[1]
    v4, v6 = local_addresses(actual_port)

    print("=" * 64)
    print(" 黄金行情服务已启动")
    print(" 监听： %s:%s　[%s]" % (real_host, actual_port, mode))
    print(" 时区： %s　当前时间： %s" % (TZNAME, now_str()))
    if real_host in ("127.0.0.1", "::1"):
        print(" 本机访问： http://localhost:%d  (仅本机可见)" % actual_port)
    else:
        print(" 访问地址：")
        print("   本机    http://localhost:%d" % actual_port)
        for a in v4:
            print("   局域网  http://%s:%d" % (a, actual_port))
        for a in v6:
            print("   IPv6    http://[%s]:%d   ← 浏览器地址必须带方括号" % (a, actual_port))
        if not v4 and not v6:
            print("   （未能枚举出网卡地址，直接用上面的监听地址拼接即可）")
    print(" 停止服务： Ctrl + C")
    print("=" * 64)
    if open_browser:
        threading.Timer(1.0, lambda: _open("http://localhost:%d" % actual_port)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        srv.shutdown()
    return 0


def _open(url):
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main())
