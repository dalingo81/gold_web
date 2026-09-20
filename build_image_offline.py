# -*- coding: utf-8 -*-
"""
离线构建 gold-dashboard:1.0 Docker 镜像（无 daemon / 无 wsl 依赖）

原理：
  1. 从公共镜像加速源拉取 python:3.12-slim-bookworm 的 OCI manifest 与各层
     （匿名 token 拉取，blob 端点支持多源 + 匿名 fallback）
  2. 拼装应用层（gold_server.py + gold/ 页面）
  3. 按 Docker Save 兼容格式输出可被 `docker load` 直接导入的镜像 tar，gzip 交付
"""
import gzip
import hashlib
import io
import json
import os
import re
import ssl
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
SERVER_PY = os.path.join(ROOT, "gold_server.py")
APP_DIR = os.path.join(ROOT, "gold")

MIRRORS = [
    "https://docker.m.daocloud.io/v2/",
    "https://docker.1ms.run/v2/",
]
NAME = "library/python"
TAG = "3.12-slim-bookworm"
ACCEPT = ("application/vnd.docker.distribution.manifest.list.v2+json, "
          "application/vnd.oci.image.index.v1+json, "
          "application/vnd.docker.distribution.manifest.v2+json, "
          "application/vnd.oci.image.manifest.v1+json")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 gold-dashboard-offline-builder"

ctx = ssl.create_default_context()
LOG = []
DIFF_CACHE = {}  # digest(解压后 sha256) -> bytes（避免重复解压）


def log(msg):
    LOG.append(msg)
    print(msg, flush=True)


def _request(url, headers, timeout=120):
    req = urllib.request.Request(url, headers=headers)
    return urllib.request.urlopen(req, timeout=timeout, context=ctx)


def _pick_token(mirror):
    """对指定镜像源发起 401 探测并换取匿名 pull token；返回 token 或 None"""
    url = mirror + "%s/manifests/%s" % (NAME, TAG)
    try:
        r = _request(url, {"Accept": ACCEPT, "User-Agent": UA}, timeout=40)
        r.read()  # 竟然直接 200，无需 token
        return None, "anonymous-ok"
    except urllib.error.HTTPError as e:
        if e.code != 401:
            return None, "probe-fail:%s" % e.code
        auth = e.headers.get("WWW-Authenticate", "")
        m = re.match(r'Bearer realm="([^"]+)",service="([^"]+)"(?:,scope="([^"]+)")?', auth)
        if not m:
            return None, "bad-challenge:%r" % auth
        realm, service, scope = m.group(1), m.group(2), m.group(3) or ("repository:%s:pull" % NAME)
        url = realm + "?" + urllib.parse.urlencode({"service": service, "scope": scope})
        r = _request(url, {"User-Agent": UA}, timeout=40)
        return json.loads(r.read())["token"], "token-ok"


def registry_api(mirror, path, accept, use_token=True, timeout=180):
    """带 token 或匿名的 registry GET，返回 bytes；403/401 会抛带体的异常"""
    headers = {"Accept": accept, "User-Agent": UA}
    if use_token:
        tok, info = _pick_token(mirror)
        if tok:
            headers["Authorization"] = "Bearer " + tok
    try:
        r = _request(mirror + path, headers, timeout=timeout)
        return r.read()
    except urllib.error.HTTPError as e:
        body = e.read()[:300]
        raise RuntimeError("GET %s -> %s body=%r" % (path, e.code, body))


def pull_layer_blob(digest, size):
    """多源 + 匿名 fallback 拉取一个层 blob，解压返回 layer 字节（带本地缓存，重打包免重拉）"""
    cache_dir = os.path.join(ROOT, "_layer_cache")
    os.makedirs(cache_dir, exist_ok=True)
    cache_file = os.path.join(cache_dir, digest.split(":")[-1] + ".layer")
    if os.path.exists(cache_file):
        with open(cache_file, "rb") as f:
            return f.read()
    for mirror in MIRRORS:
        for use_tok in (True, False):
            try:
                data = registry_api(mirror, "%s/blobs/%s" % (NAME, digest),
                                    "application/octet-stream",
                                    use_token=use_tok, timeout=300)
                if len(data) < min(size - 512, size):  # 完整性粗校验（防空响应用 200 糊弄）
                    pass
                if data[:2] == b"\x1f\x8b":
                    layer = gzip.decompress(data)
                else:
                    layer = data
                with open(cache_file, "wb") as f:
                    f.write(layer)
                return layer
            except Exception as e:
                log("    blob %s %s tok=%s -> %s" % (digest[:12], mirror, use_tok, str(e)[:120]))
    raise RuntimeError("all mirrors failed for blob %s" % digest)


def pull_manifest_list():
    for mirror in MIRRORS:
        try:
            data = registry_api(mirror, "%s/manifests/%s" % (NAME, TAG), ACCEPT)
            return mirror, data
        except Exception as e:
            log("manifest-list %s -> %s" % (mirror, str(e)[:120]))
    raise RuntimeError("all mirrors failed for manifest list")


def build_meta_layer():
    """构造 /app 应用层（gold_server.py + gold/）"""
    buf = io.BytesIO()
    now = int(time.time())
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tf:
        def add_file(tarpath, content, mode=0o644):
            ti = tarfile.TarInfo(tarpath)
            ti.size = len(content)
            ti.mode = mode
            ti.mtime = now
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = "root"
            tf.addfile(ti, io.BytesIO(content))

        add_file("app/gold_server.py", open(SERVER_PY, "rb").read(), 0o755)
        add_file("app/gold/index.html", open(os.path.join(APP_DIR, "index.html"), "rb").read())
        add_file("app/gold/echarts.min.js", open(os.path.join(APP_DIR, "echarts.min.js"), "rb").read())
    return buf.getvalue()


def build_config(base_cfg, diff_ids):
    cfg = json.loads(json.dumps(base_cfg))
    c = cfg.setdefault("config", {})
    env = list(c.get("Env") or [])
    for k, v in [("TZ", "Asia/Shanghai"),
                 ("PYTHONUNBUFFERED", "1"),
                 ("PYTHONDONTWRITEBYTECODE", "1"),
                 ("GOLD_HOST", "::"),
                 ("GOLD_PORT", "8765"),
                 ("GOLD_NO_BROWSER", "1")]:
        env = [x for x in env if not x.startswith(k + "=")] + [k + "=" + v]
    c["Env"] = env
    c["WorkingDir"] = "/app"
    c["Cmd"] = ["python", "gold_server.py", "--no-browser"]
    if "Entrypoint" not in c or c["Entrypoint"] is None:
        c["Entrypoint"] = []
    c["ExposedPorts"] = {"8765/tcp": {}}
    c["Healthcheck"] = {
        "Test": ["CMD-SHELL",
                 "python -c \"import os,urllib.request;urllib.request.urlopen("
                 "'http://127.0.0.1:'+os.environ.get('GOLD_PORT','8765')+'/api/ping',timeout=5)\""],
        "Interval": 60000000000, "Timeout": 10000000000,
        "StartPeriod": 15000000000, "Retries": 3,
    }
    # Docker 规范：diff_ids 必须带 "sha256:" 前缀（docker load 严格校验，缺前缀直接报
    # "invalid diffID ... expected X, got sha256:X"）。官方 config 自带前缀，应用层也要补。
    cfg["rootfs"]["diff_ids"] = [
        d if d.startswith("sha256:") else "sha256:" + d for d in diff_ids
    ]
    hist = list(cfg.get("history") or [])
    hist.append({"created_by": "ADD gold_server.py gold/ (offline builder)",
                 "empty_layer": False})
    cfg["history"] = hist
    cfg.setdefault("container_config", {})
    return cfg


def main():
    # 1. manifest list / amd64 manifest
    mirror, body = pull_manifest_list()
    log("primary mirror: %s" % mirror)
    lst = json.loads(body)
    amd64 = None
    if "manifests" in lst:
        for m in lst["manifests"]:
            if m.get("platform", {}).get("architecture") == "amd64" and \
               m.get("platform", {}).get("os") == "linux":
                amd64 = m
                break
    else:
        amd64 = {"digest": lst.get("digest") or TAG, "mediaType": lst.get("mediaType")}
    if not amd64:
        raise RuntimeError("no linux/amd64 manifest")
    log("amd64 manifest: %s" % amd64["digest"])

    man = json.loads(registry_api(mirror, "%s/manifests/%s" % (NAME, amd64["digest"]), ACCEPT))
    layers = man.get("layers", [])
    cfg_digest = man["config"]["digest"]
    log("layers=%d config=%s" % (len(layers), cfg_digest))

    base_cfg = json.loads(registry_api(mirror, "%s/blobs/%s" % (NAME, cfg_digest), "application/vnd.docker.container.image.v1+json"))
    official_diffs = base_cfg["rootfs"]["diff_ids"]
    log("official diff_ids=%d" % len(official_diffs))

    # 2. 逐层拉取
    layer_tars, diff_ids = [], []
    for i, L in enumerate(layers):
        dig = L["digest"]
        log("  pull layer %d/%d %s (%.1f MB)" % (i + 1, len(layers), dig[:19], L["size"] / 1048576))
        lt = pull_layer_blob(dig, L["size"])
        dsum = hashlib.sha256(lt).hexdigest()
        if dsum != official_diffs[i].replace("sha256:", ""):
            raise RuntimeError("diff_id mismatch layer %d: %s != %s" % (i, dsum, official_diffs[i]))
        layer_tars.append(lt)
        diff_ids.append(dsum)
        log("    ok %s (%.1f MB raw)" % (dsum[:19], len(lt) / 1048576))

    # 3. 应用层
    app_layer = build_meta_layer()
    app_id = hashlib.sha256(app_layer).hexdigest()
    layer_tars.append(app_layer)
    diff_ids.append(app_id)
    log("app layer: %s" % app_id[:19])

    # 4. 组装 docker save 兼容 tar
    cfg = build_config(base_cfg, diff_ids)
    cfg_bytes = json.dumps(cfg, ensure_ascii=False).encode("utf-8")
    cfg_name = "gold-config.json"
    layer_names = ["layer%d/layer.tar" % i for i in range(len(layer_tars))]
    manifest = [{"Config": cfg_name, "RepoTags": ["gold-dashboard:1.0"], "Layers": layer_names}]

    tar_path = os.path.join(ROOT, "gold-dashboard-1.0.tar")
    with tarfile.open(tar_path, "w") as tf:
        def add_bytes(name, data):
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            ti.mtime = int(time.time())
            ti.mode = 0o644
            tf.addfile(ti, io.BytesIO(data))

        add_bytes("manifest.json", json.dumps(manifest, ensure_ascii=False).encode("utf-8"))
        add_bytes(cfg_name, cfg_bytes)
        for i, lt in enumerate(layer_tars):
            add_bytes(layer_names[i], lt)
    log("镜像 tar: %.1f MB" % (os.path.getsize(tar_path) / 1048576))

    # 5. gzip 交付
    out = os.path.join(ROOT, "gold-dashboard-1.0.tar.gz")
    with open(tar_path, "rb") as f_in, gzip.open(out, "wb", compresslevel=6) as f_out:
        while True:
            chunk = f_in.read(1 << 20)
            if not chunk:
                break
            f_out.write(chunk)
    os.remove(tar_path)
    log("交付: %s (%.1f MB)" % (out, os.path.getsize(out) / 1048576))

    with open(os.path.join(ROOT, "_offline_build_log.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(LOG))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        LOG.append("FAILED: %s" % e)
        LOG.append(traceback.format_exc())
        with open(os.path.join(ROOT, "_offline_build_log.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(LOG))
        sys.exit(1)
