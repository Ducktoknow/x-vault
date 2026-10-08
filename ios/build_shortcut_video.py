#!/usr/bin/env python3
"""Create an iPhone Shortcut: X and TikTok videos locally, X text to Notion.

Reuses the previously proven X Vault share-sheet POST and import questions.
Video download is opt-in via JSON video_action=download: older installed
Shortcuts continue to behave exactly as before.

To share on iPhone, sign the generated file on your own Mac:
    python3 ios/build_shortcut_video.py
    shortcuts sign --mode anyone \\
      --input ios/X-Vault-Video-Local.unsigned.shortcut \\
      --output ios/X-Vault-Video-Local.shortcut

Do not put a real Render APP_TOKEN in this public template.
"""
import plistlib
from pathlib import Path

from build_shortcut import (
    action, build, direct_var, field, output_var, replacement_token,
    text_token, uid
)


def build_video_shortcut():
    workflow = build()
    token_action, request, getmessage, notify = workflow["WFWorkflowActions"]

    # The endpoint itself remains a literal, import-configurable HTTPS URL.
    # This avoids the broken magic URL variable seen on some iOS imports.
    entries = request["WFWorkflowActionParameters"]["WFJSONValues"]["Value"]["WFDictionaryFieldValueItems"]
    entries.append(field("video_action", text_token("download")))

    request_response = direct_var(output_var(
        request["WFWorkflowActionParameters"]["UUID"], "Contents of URL"))
    status = action(
        "getvalueforkey", WFDictionaryKey="status",
        WFGetDictionaryValueType="Value", WFInput=request_response)
    # Text coercion for a stable text comparator in If.
    status_text = action(
        "gettext", WFTextActionText=replacement_token(
            output_var(status["WFWorkflowActionParameters"]["UUID"], "Dictionary Value")))
    group = uid()
    if_video = action(
        "conditional", WFControlFlowMode=0, WFCondition=99,
        WFConditionalActionString="download_ready",
        WFInput={"Type": "Variable", "Variable": direct_var(
            output_var(status_text["WFWorkflowActionParameters"]["UUID"], "Text"))},
        GroupingIdentifier=group)

    get_download_url = action(
        "getvalueforkey", WFDictionaryKey="download_url",
        WFGetDictionaryValueType="Value", WFInput=request_response)
    # The video link must be read from the JSON response; it is unique,
    # valid only for five minutes and consumed on its first GET.
    download_video = action(
        "downloadurl",
        WFURL=replacement_token(output_var(
            get_download_url["WFWorkflowActionParameters"]["UUID"], "Dictionary Value")),
        WFHTTPMethod="GET")
    save_to_files = action(
        "documentpicker.save",
        WFInput=direct_var(output_var(
            download_video["WFWorkflowActionParameters"]["UUID"], "Contents of URL")),
        WFAskWhereToSave=True)
    saved_notification = action(
        "notification",
        WFNotificationActionTitle="X Vault",
        WFNotificationActionBody="✅ 视频已保存到 iPhone「文件」",
        WFNotificationActionSound=True)

    otherwise = action("conditional", WFControlFlowMode=1,
                       GroupingIdentifier=group)
    end_if = action("conditional", WFControlFlowMode=2,
                    GroupingIdentifier=group)

    workflow["WFWorkflowName"] = "收藏到 X Vault · 视频存本机"
    workflow["WFWorkflowActions"] = [
        token_action, request, status, status_text, if_video,
        get_download_url, download_video, save_to_files, saved_notification,
        otherwise, getmessage, notify, end_if,
    ]
    # First two action indexes remain 0/1, so original import prompts work.
    return workflow


if __name__ == "__main__":
    path = Path(__file__).resolve().parent / "X-Vault-Video-Local.unsigned.shortcut"
    with path.open("wb") as out:
        plistlib.dump(build_video_shortcut(), out, fmt=plistlib.FMT_BINARY,
                      sort_keys=False)
    print(f"Created: {path}")