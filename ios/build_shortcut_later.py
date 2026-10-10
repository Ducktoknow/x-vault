#!/usr/bin/env python3
"""Build a one-tap X/TikTok bookmark shortcut with no video downloads."""
import plistlib
from pathlib import Path
from build_shortcut import build


def build_later_shortcut():
    workflow = build()
    workflow["WFWorkflowName"] = "收藏到 X Vault · 稍后看"
    return workflow

if __name__ == "__main__":
    target = Path(__file__).with_name("X-Vault-Read-Later.unsigned.shortcut")
    target.write_bytes(plistlib.dumps(build_later_shortcut(), fmt=plistlib.FMT_BINARY, sort_keys=False))
    print("Created:", target)