#!/usr/bin/env python3
import json
import sys


def positive_bytes(value):
    if isinstance(value, str) and value.isascii() and value.isdigit():
        value = int(value)
    return value if type(value) is int and value > 0 else None


def validate(config):
    services = config.get("services") if isinstance(config, dict) else None
    if not isinstance(services, dict) or not services:
        return ["production Compose must contain services"]
    errors = []
    for name, service in services.items():
        if not isinstance(service, dict):
            errors.append(f"{name}: invalid service")
            continue
        memory = positive_bytes(service.get("mem_limit"))
        total = positive_bytes(service.get("memswap_limit"))
        if memory is None:
            errors.append(f"{name}: explicit positive mem_limit is required")
        if total is None:
            errors.append(f"{name}: explicit positive memswap_limit is required")
        elif memory is not None and total < memory:
            errors.append(f"{name}: memswap_limit must include the RAM budget")
    return errors


def main():
    try:
        errors = validate(json.load(sys.stdin))
    except (ValueError, OverflowError):
        errors = ["invalid Compose JSON"]
    for error in errors:
        print(error, file=sys.stderr)
    return bool(errors)


if __name__ == "__main__":
    sys.exit(main())
