#!/usr/bin/env python3
"""
Reliable ntfy.sh approval listener for Daily OSS Contributor.
Avoids pipe buffering and timing window bugs by polling with epoch cutoff.
Works across launchd daemon environments by handling macOS certificate bundles.
"""

import sys
import os
import time
import json
import ssl
import subprocess
import argparse
import urllib.request

DEFAULT_TOPIC = os.environ.get("NTFY_TOPIC", "kasa-oss-contributor")


def get_ssl_context():
    # Attempt standard context first; fallback to unverified context to ensure launchd daemons never fail
    try:
        ctx = ssl.create_default_context()
        if os.path.exists("/etc/ssl/cert.pem"):
            ctx.load_verify_locations(cafile="/etc/ssl/cert.pem")
        return ctx
    except Exception:
        return ssl._create_unverified_context()


def fetch_events_urllib(url, ctx):
    req = urllib.request.Request(url, headers={"User-Agent": "daily-oss-contributor/1.0"})
    with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
        return resp.read().decode("utf-8")


def fetch_events_curl(url):
    res = subprocess.run(["curl", "-s", "--max-time", "15", url], capture_output=True, text=True)
    return res.stdout if res.returncode == 0 else ""


def main():
    parser = argparse.ArgumentParser(description="Wait for ntfy approval signal")
    parser.add_argument("--topic", default=DEFAULT_TOPIC, help="ntfy.sh topic to poll")
    parser.add_argument("--since-epoch", type=int, default=0, help="Unix epoch cutoff: ignore messages before this time")
    parser.add_argument("--timeout", type=int, default=7200, help="Maximum seconds to wait (default: 7200)")
    parser.add_argument("--interval", type=int, default=3, help="Polling interval in seconds")
    args = parser.parse_args()

    topic = args.topic or DEFAULT_TOPIC
    start_time = time.time()
    cutoff = args.since_epoch if args.since_epoch > 0 else int(start_time - 900)
    ctx = get_ssl_context()

    while (time.time() - start_time) < args.timeout:
        url = f"https://ntfy.sh/{topic}/json?poll=1&since={cutoff}"
        raw_data = ""

        # Primary: urllib with macOS-safe SSL context
        try:
            raw_data = fetch_events_urllib(url, ctx)
        except Exception:
            # Secondary: native curl fallback
            try:
                raw_data = fetch_events_curl(url)
            except Exception:
                pass

        if raw_data:
            for line in raw_data.splitlines():
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    if data.get("event") == "message":
                        msg_time = data.get("time", 0)
                        msg_text = (data.get("message") or "").strip().upper()
                        if msg_time >= cutoff and msg_text in ("APPROVE", "REJECT"):
                            print(msg_text)
                            sys.exit(0)
                except Exception:
                    pass

        time.sleep(args.interval)

    print("TIMEOUT")
    sys.exit(1)


if __name__ == "__main__":
    main()
