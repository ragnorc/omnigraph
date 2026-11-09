#!/usr/bin/env python3
"""Minimal SigV4 S3 client (stdlib) for the local RustFS: create a bucket,
get, put and delete an object. Usage:
  s3util.py mb BUCKET | get BUCKET KEY | put BUCKET KEY FILE | rm BUCKET KEY | ls BUCKET PREFIX
"""
import datetime, hashlib, hmac, os, sys, urllib.parse, http.client

ENDPOINT = os.environ.get("AWS_ENDPOINT_URL_S3", "http://127.0.0.1:9000")
AK = os.environ.get("AWS_ACCESS_KEY_ID", "omnigraphci")
SK = os.environ.get("AWS_SECRET_ACCESS_KEY", "omnigraphci-secret")
REGION = os.environ.get("AWS_REGION", "us-east-1")


def sign(key, msg):
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def request(method, path, body=b"", query=""):
    u = urllib.parse.urlparse(ENDPOINT)
    now = datetime.datetime.now(datetime.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(body).hexdigest()
    host = u.netloc
    canonical_query = "&".join(sorted(f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}" for k, v in urllib.parse.parse_qsl(query, keep_blank_values=True)))
    headers = {"host": host, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date}
    signed = ";".join(sorted(headers))
    canonical = "\n".join([method, urllib.parse.quote(path), canonical_query,
                           "".join(f"{k}:{headers[k]}\n" for k in sorted(headers)), signed, payload_hash])
    scope = f"{date}/{REGION}/s3/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    k = sign(sign(sign(sign(("AWS4" + SK).encode(), date), REGION), "s3"), "aws4_request")
    sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    headers["authorization"] = f"AWS4-HMAC-SHA256 Credential={AK}/{scope}, SignedHeaders={signed}, Signature={sig}"
    conn = http.client.HTTPConnection(u.hostname, u.port, timeout=30)
    conn.request(method, path + ("?" + query if query else ""), body=body, headers=headers)
    r = conn.getresponse()
    data = r.read()
    return r.status, data


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "mb":
        print(request("PUT", f"/{sys.argv[2]}"))
    elif cmd == "get":
        s, d = request("GET", f"/{sys.argv[2]}/{sys.argv[3]}")
        sys.stdout.buffer.write(d) if s == 200 else print(s, d[:300])
    elif cmd == "put":
        print(request("PUT", f"/{sys.argv[2]}/{sys.argv[3]}", open(sys.argv[4], "rb").read()))
    elif cmd == "rm":
        print(request("DELETE", f"/{sys.argv[2]}/{sys.argv[3]}"))
    elif cmd == "ls":
        print(request("GET", f"/{sys.argv[2]}", query=f"list-type=2&prefix={sys.argv[3]}")[1].decode()[:5000])
