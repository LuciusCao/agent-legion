#!/usr/bin/env python3
"""诊断 CMS by_knowledge 查询。用法：
python scripts/diagnose_cms.py <knowledge_code>
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from server.app.cms.client import get_token
from server.app.cms.question import list_questions_by_knowledge
from server.app.settings import load_settings
from server.app.workflows.resources import resolve_cms_resource


def main(knowledge_code: str) -> None:
    settings = load_settings()
    workspace = {
        "resource_config": {
            "resources": {
                "by_knowledge": {
                    "config": {
                        "bank_version": "v5",
                        "country_id": "1",
                        "page_size": "50",
                        "subject_id": "2",
                    },
                    "provider": "cms.question.list_by_knowledge",
                }
            }
        }
    }

    resource = resolve_cms_resource(settings.config, workspace, None, "by_knowledge")
    api_url = resource.get("api_url") or resource.get("question_list_url")
    token = get_token(str(resource.get("env", "")), resource)

    print(f"API URL: {api_url}")
    print(f"Token: {'已获取' if token else '未获取'}")
    print(f"Knowledge code: {knowledge_code}")
    print()

    summaries = list_questions_by_knowledge(knowledge_code, api_url, token)
    print(f"返回 {len(summaries)} 条题目")
    for s in summaries[:10]:
        print(f"  {s.question_id}: {s.title}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/diagnose_cms.py <knowledge_code>")
        sys.exit(1)
    main(sys.argv[1])
