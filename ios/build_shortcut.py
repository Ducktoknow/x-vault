#!/usr/bin/env python3
"""Build a no-credentials Apple Shortcuts workflow for X Vault.

Sign generated unsigned binary plist on macOS using:
    shortcuts sign --mode anyone --input X-Vault-Share.unsigned.shortcut --output X-Vault-Share.shortcut
The signed file has import-time questions for Render URL and APP_TOKEN.
Requires a real-device smoke test: macOS signing verifies authenticity, not iPhone execution.
"""
import plistlib
import uuid
from pathlib import Path


def uid():
    return str(uuid.uuid4()).upper()


def text_token(value):
    return {"Value": {"string": value}, "WFSerializationType": "WFTextTokenString"}


def output_var(action_uuid, output):
    return {"Type": "ActionOutput", "OutputUUID": action_uuid, "OutputName": output}


def replacement_token(var, prefix=""):
    position = len(prefix.encode("utf-16-le")) // 2
    return {"Value": {
        "string": prefix + "\ufffc",
        "attachmentsByRange": {f"{{{position}, 1}}": var}
    }, "WFSerializationType": "WFTextTokenString"}


def direct_var(var):
    return {"Value": var, "WFSerializationType": "WFTextTokenAttachment"}


def field(name, value):
    return {"WFItemType": 0, "WFKey": text_token(name), "WFValue": value}


def dictionary(items):
    return {"Value": {"WFDictionaryFieldValueItems": items},
            "WFSerializationType": "WFDictionaryFieldValue"}


def action(identifier, **kwargs):
    return {"WFWorkflowActionIdentifier": "is.workflow.actions." + identifier,
            "WFWorkflowActionParameters": {"UUID": uid(), **kwargs}}


def build():
    # Do not pass a magic variable into Get Contents of URL: iOS may fail
    # to resolve it after importing a signed shortcut. Keep a literal,
    # syntactically valid HTTPS URL and prompt the user to replace it at import.
    token_action = action("gettext", WFTextActionText="PASTE_YOUR_APP_TOKEN_HERE")
    request = action(
        "downloadurl",
        WFURL="https://x-vault.onrender.com/api/shortcut/save",
        WFHTTPMethod="POST", WFHTTPBodyType="JSON", ShowHeaders=True,
        WFHTTPHeaders=dictionary([
            field("Authorization", replacement_token(output_var(token_action["WFWorkflowActionParameters"]["UUID"], "Text"), "Bearer ")),
            field("Content-Type", text_token("application/json"))
        ]),
        WFJSONValues=dictionary([
            field("url", replacement_token({"Type": "ExtensionInput"}))
        ]),
    )
    getmessage = action(
        "getvalueforkey", WFDictionaryKey="message",
        WFGetDictionaryValueType="Value",
        WFInput=direct_var(output_var(request["WFWorkflowActionParameters"]["UUID"], "Contents of URL")),
    )
    notify = action(
        "notification", WFNotificationActionTitle="X Vault",
        WFNotificationActionBody=replacement_token(output_var(getmessage["WFWorkflowActionParameters"]["UUID"], "Dictionary Value")),
        WFNotificationActionSound=True
    )
    workflow = {
        "WFWorkflowName": "收藏到 X Vault",
        "WFWorkflowClientVersion": "2607.1.3",
        "WFWorkflowClientRelease": "26.0",
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowIcon": {"WFWorkflowIconStartColor": 946986751, "WFWorkflowIconGlyphNumber": 61440},
        "WFWorkflowActions": [token_action, request, getmessage, notify],
        "WFWorkflowInputContentItemClasses": ["WFURLContentItem", "WFStringContentItem", "WFTextContentItem"],
        "WFWorkflowOutputContentItemClasses": [],
        "WFWorkflowTypes": ["ActionExtension"],
        "WFWorkflowHasShortcutInputVariables": True,
        "WFWorkflowHasOutputFallback": False,
        "WFWorkflowImportQuestions": [
            {
                "ActionIndex": 1,
                "Category": "Parameter",
                "ParameterKey": "WFURL",
                "Text": "你的 Render 接口网址（要以 /api/shortcut/save 结尾）",
                "DefaultValue": "https://x-vault.onrender.com/api/shortcut/save"
            },
            {
                "ActionIndex": 0,
                "Category": "Parameter",
                "ParameterKey": "WFTextActionText",
                "Text": "请输入 Render 的 APP_TOKEN（只输入密钥，不加 Bearer）",
                "DefaultValue": "PASTE_YOUR_APP_TOKEN_HERE"
            }
        ]
    }
    return workflow


if __name__ == "__main__":
    folder = Path(__file__).resolve().parent
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / "X-Vault-Share.unsigned.shortcut"
    with output.open("wb") as f:
        plistlib.dump(build(), f, fmt=plistlib.FMT_BINARY, sort_keys=False)
    print("Created:", output)
    print("Actions:", len(plistlib.loads(output.read_bytes())["WFWorkflowActions"]))