"""Minimal Azurite Blob client with SharedKey signing (stdlib only).

The key is Azurite's public development key, never a production credential.
"""
import base64, datetime, email.utils, hashlib, hmac, http.client, urllib.parse

ACCOUNT = "devstoreaccount1"
KEY = base64.b64decode("Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/"
                       "K1SZFPTOtr/KBHBeksoGMGw==")
HOST, PORT = "127.0.0.1", 10000
VERSION = "2023-11-03"


def request(method, path, query=None, body=b"", headers=None, port=PORT):
    """`path` is `/container[/blob]` (without the account segment)."""
    query = query or {}
    headers = dict(headers or {})
    date = email.utils.format_datetime(datetime.datetime.now(datetime.timezone.utc), usegmt=True)
    headers["x-ms-date"] = date
    headers["x-ms-version"] = VERSION
    length = str(len(body)) if body else ""
    canon_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(h for h in headers if h.startswith("x-ms-")))
    canon_resource = f"/{ACCOUNT}/{ACCOUNT}{urllib.parse.quote(path)}"
    for k in sorted(query):
        canon_resource += f"\n{k}:{query[k]}"
    sts = "\n".join([method, "", "", length, "", headers.get("Content-Type", ""), "", "", headers.get("If-Match", ""),
                     headers.get("If-None-Match", ""), "", headers.get("Range", "")]) + "\n" + canon_headers + canon_resource
    sig = base64.b64encode(hmac.new(KEY, sts.encode(), hashlib.sha256).digest()).decode()
    headers["Authorization"] = f"SharedKey {ACCOUNT}:{sig}"
    url = f"/{ACCOUNT}{urllib.parse.quote(path)}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    conn = http.client.HTTPConnection(HOST, port, timeout=30)
    conn.request(method, url, body=body or None, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, data


def ensure_container(name):
    status, data = request("PUT", f"/{name}", {"restype": "container"})
    if status not in (201, 409):
        raise RuntimeError(f"create container {status} {data[:300]}")


def get_blob(container, blob):
    return request("GET", f"/{container}/{blob}")
