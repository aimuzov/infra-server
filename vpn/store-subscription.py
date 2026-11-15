#!/usr/bin/env python3
import json
import os
import shlex
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


def store(stream, target):
    payload = stream.read(16385)
    if not payload.strip():
        return
    if len(payload) > 16384:
        raise ValueError("invalid subscription payload")
    try:
        data = json.loads(payload)
        url = data["vpn_subscription_url"]
        parsed = urlsplit(url)
        if (not isinstance(url, str) or parsed.scheme != "https" or not parsed.hostname
                or parsed.username or parsed.password or any(ord(c) < 32 for c in url)):
            raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError("invalid subscription payload") from None
    descriptor, filename = tempfile.mkstemp(prefix=".subscription-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w") as output:
            output.write("VPN_SUBSCRIPTION_URL=" + shlex.quote(url) + "\n")
        os.replace(filename, target)
    finally:
        if os.path.exists(filename):
            os.unlink(filename)


if __name__ == "__main__":
    try:
        store(sys.stdin, Path(__file__).resolve().parent / "subscription.env")
    except (ValueError, OSError):
        sys.exit("vpn: could not save subscription secret")
