"""
feature_llm_report.md を Notion へアップロードするスクリプト
Cell P_Notion の代替実行
"""
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_PARENT_PAGE_ID = os.getenv("NOTION_PARENT_PAGE_ID")
NOTION_VERSION = "2022-06-28"

ROOT = Path(__file__).parent.parent
DEFAULT_REPORTS = [
    ROOT / "reports" / "scenario_router_audit_report.md",
    ROOT / "reports" / "scenario_router_audit_trend.md",
    ROOT / "docs" / "reports" / "feature_llm_report.md",
]


if not NOTION_TOKEN:
    raise RuntimeError("NOTION_TOKEN is not set")
if not NOTION_PARENT_PAGE_ID:
    raise RuntimeError("NOTION_PARENT_PAGE_ID is not set")


def notion_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def chunk_text(text: str, size: int = 1800) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


def append_text_to_page(page_id: str, title: str, body: str):
    blocks = [
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{"type": "text", "text": {"content": title}}]
            },
        }
    ]

    for part in chunk_text(body):
        blocks.append(
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": part}}]
                },
            }
        )

    url = f"https://api.notion.com/v1/blocks/{page_id}/children"
    res = requests.patch(url, headers=notion_headers(), json={"children": blocks}, timeout=60)
    if not res.ok:
        raise RuntimeError(f"Notion API error: {res.status_code} {res.text}")
    return res.json()


def _pick_report_file() -> Path:
    override = os.getenv("NOTION_REPORT_PATH")
    if override:
        return Path(override)
    for candidate in DEFAULT_REPORTS:
        if candidate.exists():
            return candidate
    return DEFAULT_REPORTS[0]


if __name__ == "__main__":
    output_file = _pick_report_file()
    if output_file.exists():
        body = output_file.read_text(encoding="utf-8")
    else:
        body = "# keibaAI output\n\nNo report file found."

    append_text_to_page(
        page_id=NOTION_PARENT_PAGE_ID,
        title="keibaAI Output",
        body=body,
    )
    print("Uploaded keibaAI output to Notion.")
