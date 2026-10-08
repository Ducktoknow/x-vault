#!/usr/bin/env python3
"""Create an iPhone Shortcut: MP4 videos to Photos, other files to Files.

X and TikTok videos share one download flow; text/images stay in Notion.

Reuses the previously proven X Vault share-sheet POST and import questions.
Video download is opt-in via JSON video_action=download: older installed
Shortcuts continue to behave exactly as before.

To share on iPhone, sign the generated file on your own Mac:
    python3 ios/build_shortcut_video.py
    shortcuts sign --mode anyone \\
      --input ios/X-Vault-Video-Photos.unsigned.shortcut \\
      --output ios/X-Vault-Video-Photos.shortcut

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
    # Photos accepts ordinary MP4 downloads. Non-MP4 attachments may be
    # ZIPs (multi-video/partial failure) or WebM/MKV; keep those in Files
    # rather than trying to save a non-photo item to the Photos library.
    file_type = action(
        "properties.files", WFContentItemPropertyName="File Extension",
        WFInput=direct_var(output_var(
            download_video["WFWorkflowActionParameters"]["UUID"], "Contents of URL")))
    file_type_text = action(
        "gettext", WFTextActionText=replacement_token(output_var(
            file_type["WFWorkflowActionParameters"]["UUID"], "File Extension")))
    format_group = uid()
    if_mp4 = action(
        "conditional", WFControlFlowMode=0, WFCondition=4,
        WFConditionalActionString="mp4",
        WFInput={"Type": "Variable", "Variable": direct_var(output_var(
            file_type_text["WFWorkflowActionParameters"]["UUID"], "Text"))},
        GroupingIdentifier=format_group)
    save_to_photos = action(
        "savetocameraroll",
        WFInput=direct_var(output_var(
            download_video["WFWorkflowActionParameters"]["UUID"], "Contents of URL")))
    photos_notification = action(
        "notification", WFNotificationActionTitle="X Vault",
        WFNotificationActionBody="✅ 视频已保存到 iPhone「照片」相册",
        WFNotificationActionSound=True)
    other_format = action("conditional", WFControlFlowMode=1,
                          GroupingIdentifier=format_group)
    save_to_files = action(
        "documentpicker.save",
        WFInput=direct_var(output_var(
            download_video["WFWorkflowActionParameters"]["UUID"], "Contents of URL")),
        WFAskWhereToSave=True)
    files_notification = action(
        "notification",
        WFNotificationActionTitle="X Vault",
        WFNotificationActionBody="ℹ️ 此文件不是 MP4，已存到「文件」（ZIP/WebM 等无法直接写入相册）",
        WFNotificationActionSound=True)
    end_format = action("conditional", WFControlFlowMode=2,
                        GroupingIdentifier=format_group)

    otherwise = action("conditional", WFControlFlowMode=1,
                       GroupingIdentifier=group)
    end_if = action("conditional", WFControlFlowMode=2,
                    GroupingIdentifier=group)

    workflow["WFWorkflowName"] = "收藏到 X Vault · 视频存相册"
    workflow["WFWorkflowActions"] = [
        token_action, request, status, status_text, if_video,
        get_download_url, download_video, file_type, file_type_text,
        if_mp4, save_to_photos, photos_notification,
        other_format, save_to_files, files_notification, end_format,
        otherwise, getmessage, notify, end_if,
    ]
    # First two action indexes remain 0/1, so original import prompts work.
    return workflow


if __name__ == "__main__":
    path = Path(__file__).resolve().parent / "X-Vault-Video-Photos.unsigned.shortcut"
    with path.open("wb") as out:
        plistlib.dump(build_video_shortcut(), out, fmt=plistlib.FMT_BINARY,
                      sort_keys=False)
    print(f"Created: {path}")