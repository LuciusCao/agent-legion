#!/usr/bin/env python3
"""Apply a workflow seed package to a target instance over the HTTP API.

Idempotent: every step content-compares against the target before writing
(canonical JSON for definitions, byte equality for code texts, ref+commit
for skill pins), so a second run against the same instance performs zero
writes and creates no new versions. ``--dry-run`` prints the plan without
writing anything.

Steps:

  1. workspace binding (--workspace "Name=workflow_key[:entity]", repeatable;
     workspaces already bound to a seed workflow are picked up automatically;
     new workspaces are created blank — schema v50 retired workflow
     registration: a workflow is just the DAG published into a workspace)
  2. Agent publish (workspace-scoped since schema v46: each Agent is
     published into every workspace bound to a workflow that references its
     capability; identical published definitions are skipped)
  3. first workflow revision publish for bound workspaces that have none
  4. node code publish (per bound workspace x node_codes[] entry; identical
     published code texts are skipped)
  5. skill source upsert + relock (skipped when ref and locked commit match)
  6. verification report (non-zero exit on any FAIL)

A legacy ``executors`` section in the seed is ignored: the executor concept
was retired in schema v47 (P-0.5); non-Agent-routed nodes run on the
implicit code pool.

Known platform gap (fresh deployments): publishing a workflow's FIRST
revision requires every code node to have published node code, while the
node-code API requires an active revision — so step 3 cannot bootstrap a
brand-new workspace whose workflow has custom-code-only nodes. Instances
that already have an active revision (the prod -> develop migration case)
are unaffected.

Usage (repo root):

    uv run python -m scripts.seed.import_seed \
        --base-url http://127.0.0.1:8011 --username admin --password '***' \
        --seed seed.json [--workspace "My Team=my_pipeline:entity"] [--dry-run]
"""

from __future__ import annotations

import argparse
import getpass
from pathlib import Path
from typing import Any

import requests
import yaml

from scripts.seed.seed_common import (
    canonical_json,
    content_equal,
    load_seed,
    workflow_capabilities,
)

CSRF_HEADERS = {"x-agent-legion-request": "1"}


class Client:
    def __init__(self, base_url: str, dry_run: bool) -> None:
        self.base_url = base_url.rstrip("/")
        self.dry_run = dry_run
        self.session = requests.Session()
        self.actions: list[str] = []

    def login(self, username: str, password: str) -> dict[str, Any]:
        resp = self.session.post(
            f"{self.base_url}/api/auth/login",
            json={"username": username, "password": password},
            headers=CSRF_HEADERS,
            timeout=30,
        )
        if resp.status_code != 200:
            raise SystemExit(f"login failed: HTTP {resp.status_code} {resp.text[:300]}")
        user = resp.json().get("user") or {}
        if user.get("role") != "admin":
            raise SystemExit(f"user {username!r} is not an admin (role={user.get('role')})")
        return user

    def get(self, path: str, *, allow_404: bool = False, params: dict | None = None) -> Any:
        resp = self.session.get(f"{self.base_url}{path}", params=params, timeout=30)
        if resp.status_code == 404 and allow_404:
            return None
        if resp.status_code != 200:
            raise SystemExit(f"GET {path}: HTTP {resp.status_code} {resp.text[:300]}")
        return resp.json()

    def mutate(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        params: dict | None = None,
        expect: tuple[int, ...] = (200,),
        timeout: int = 30,
        dry_note: str | None = None,
    ) -> Any:
        """Write operation; under dry-run only records the action."""
        if self.dry_run:
            self.actions.append(f"WOULD {method} {path}" + (f" — {dry_note}" if dry_note else ""))
            return None
        resp = self.session.request(
            method,
            f"{self.base_url}{path}",
            json=body,
            params=params,
            headers=CSRF_HEADERS,
            timeout=timeout,
        )
        if resp.status_code not in expect:
            raise SystemExit(f"{method} {path}: HTTP {resp.status_code} {resp.text[:500]}")
        self.actions.append(f"DID   {method} {path}" + (f" — {dry_note}" if dry_note else ""))
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()


# ---------------------------------------------------------------------------
# workflow definition drift comparison: seed dict form vs API payload form
# ---------------------------------------------------------------------------


def _seed_workflow_signature(definition: dict[str, Any]) -> dict[str, Any]:
    nodes = {
        key: (
            str(node.get("capability")),
            tuple(node.get("inputs") or []),
            tuple(node.get("outputs") or []),
            tuple(node.get("after") or []),
            (node.get("terminal") or {}).get("outcome"),
        )
        for key, node in (definition.get("nodes") or {}).items()
    }
    edges = sorted(
        (str(e.get("from")), str(e.get("to")), canonical_json(e.get("when")))
        for e in (definition.get("edges") or [])
    )
    return {"nodes": nodes, "edges": edges}


def _api_workflow_signature(payload: dict[str, Any]) -> dict[str, Any]:
    nodes = {
        str(n.get("key")): (
            str(n.get("capability")),
            tuple(n.get("inputs") or []),
            tuple(n.get("outputs") or []),
            tuple(n.get("after") or []),
            (n.get("terminal") or {}).get("outcome"),
        )
        for n in (payload.get("nodes") or [])
    }
    edges = sorted(
        (str(e.get("source")), str(e.get("target")), canonical_json(e.get("condition")))
        for e in (payload.get("edges") or [])
    )
    return {"nodes": nodes, "edges": edges}


# ---------------------------------------------------------------------------
# steps
# ---------------------------------------------------------------------------


def parse_workspace_spec(spec: str) -> tuple[str, str, str | None]:
    name, sep, rest = spec.partition("=")
    if not sep or not name.strip() or not rest.strip():
        raise SystemExit(f"--workspace spec must be 'name=workflow_key[:entity]': {spec!r}")
    workflow_key, _, entity = rest.partition(":")
    return name.strip(), workflow_key.strip(), entity.strip() or None


def step1_workspaces(
    client: Client, seed: dict[str, Any], specs: list[str], failures: list[str]
) -> dict[str, list[str]]:
    """Bind workspaces to seed workflows; returns workflow_key -> [workspace_id]."""
    print("== step 1: workspace binding ==")
    listed = client.get("/api/workspaces") or {}
    workspaces = listed.get("workspaces") or []
    bound: dict[str, list[str]] = {w["key"]: [] for w in seed["workflows"]}
    for workspace in workspaces:
        key = str(workspace.get("default_workflow_key") or "")
        if key in bound:
            bound[key].append(str(workspace["id"]))

    for spec in specs:
        name, workflow_key, entity = parse_workspace_spec(spec)
        if workflow_key not in bound:
            failures.append(f"workspace spec {spec!r}: workflow not in seed")
            continue
        if bound[workflow_key]:
            for workspace_id in bound[workflow_key]:
                print(f"  {workflow_key}: workspace {workspace_id} already bound, skip create")
            continue
        # Blank creation: nothing seeds — the seed's own definition is
        # published as revision v1 in step 3 (and its agents in step 2).
        body: dict[str, Any] = {
            "name": name,
            "default_workflow_key": workflow_key,
            "workflow_mode": "blank",
        }
        if entity is not None:
            body["default_entity"] = entity
        created = client.mutate(
            "POST",
            "/api/workspaces",
            body,
            dry_note=f"create workspace {name!r} bound to {workflow_key}",
        )
        if client.dry_run:
            print(f"  {workflow_key}: no bound workspace -> would create {name!r}")
            bound[workflow_key].append(f"<new:{workflow_key}>")
            continue
        workspace_id = str((created or {}).get("workspace", {}).get("id") or "")
        if not workspace_id:
            failures.append(f"create workspace {name!r}: response missing id")
            continue
        bound[workflow_key].append(workspace_id)
        print(f"  {workflow_key}: created workspace {workspace_id}")
    for workflow_key, workspace_ids in bound.items():
        if not workspace_ids:
            print(f"  {workflow_key}: no bound workspace (pass --workspace to create one)")
    return bound


def _agent_target_workspaces(
    seed: dict[str, Any], bound: dict[str, list[str]], capability: str
) -> list[str]:
    targets: list[str] = []
    for workflow in seed["workflows"]:
        definition = workflow.get("definition") or {}
        if capability in workflow_capabilities(definition):
            targets.extend(bound.get(workflow["key"]) or [])
    return sorted(set(targets))


def step2_agents(
    client: Client, seed: dict[str, Any], bound: dict[str, list[str]], failures: list[str]
) -> None:
    print("== step 2: Agent publish ==")
    for agent in seed.get("agents") or []:
        agent_id = agent["agent_id"]
        capability = str(agent["definition"].get("capability"))
        targets = _agent_target_workspaces(seed, bound, capability)
        if not targets:
            failures.append(f"agent {agent_id}: no bound workspace for capability {capability}")
            continue
        for workspace_id in targets:
            if workspace_id.startswith("<new:"):
                client.actions.append(
                    f"WOULD publish agent {agent_id} on new workspace of {workspace_id[5:-1]}"
                )
                continue
            label = f"{workspace_id}/{agent_id}"
            params = {"workspace_id": workspace_id}
            detail = client.get(f"/api/agent-definitions/{agent_id}", allow_404=True, params=params)
            desired = agent["definition"]
            if detail is None:
                client.mutate(
                    "POST",
                    "/api/agent-definitions",
                    {"agent_id": agent_id, **desired},
                    params=params,
                    dry_note=f"create agent {label} ({capability}) as draft",
                )
                result = client.mutate(
                    "POST",
                    f"/api/agent-definitions/{agent_id}/publish",
                    params=params,
                    dry_note=f"publish agent {label}",
                )
                print(f"  {label}: absent -> create + publish (v{(result or {}).get('version')})")
                continue
            published = detail.get("published")
            if published is not None and content_equal(published.get("definition"), desired):
                print(f"  {label}: skip (published v{published.get('version')} identical)")
                continue
            client.mutate(
                "PUT",
                f"/api/agent-definitions/{agent_id}/draft",
                desired,
                params=params,
                dry_note=f"save draft for {label}",
            )
            result = client.mutate(
                "POST",
                f"/api/agent-definitions/{agent_id}/publish",
                params=params,
                dry_note=f"publish {label}",
            )
            print(f"  {label}: definition drift -> published v{(result or {}).get('version')}")


def step3_first_revisions(
    client: Client, seed: dict[str, Any], bound: dict[str, list[str]], failures: list[str]
) -> None:
    print("== step 3: first workflow revision ==")
    workflow_defs = {w["key"]: w["definition"] for w in seed["workflows"]}
    for workflow_key, workspace_ids in bound.items():
        for workspace_id in workspace_ids:
            if workspace_id.startswith("<new:"):
                client.actions.append(
                    f"WOULD publish first workflow revision for new workspace of {workflow_key}"
                )
                continue
            active = client.get(
                f"/api/workspaces/{workspace_id}/workflow-revisions/active", allow_404=True
            )
            if active is not None:
                revision = active.get("revision") or {}
                print(
                    f"  {workspace_id}/{workflow_key}: active revision v{revision.get('version')} ok"
                )
                continue
            result = client.mutate(
                "POST",
                f"/api/workspaces/{workspace_id}/workflow-drafts/publish",
                {
                    "definition_yaml": yaml.safe_dump(
                        workflow_defs[workflow_key], allow_unicode=True, sort_keys=False
                    )
                },
                dry_note=f"publish first revision for {workspace_id}/{workflow_key}",
            )
            if client.dry_run:
                continue
            if result and result.get("valid"):
                print(f"  {workspace_id}/{workflow_key}: first revision published")
            else:
                errors = (result or {}).get("errors") or []
                failures.append(
                    f"{workspace_id}/{workflow_key}: draft publish failed: {errors} "
                    "(publish requires every code node to have published node code, "
                    "while the node-code API requires an active revision — a fresh "
                    "workspace with custom-code-only nodes cannot bootstrap via API yet; "
                    "see the module docstring 'known platform gap')"
                )


def step4_node_codes(
    client: Client, seed: dict[str, Any], bound: dict[str, list[str]], failures: list[str]
) -> None:
    print("== step 4: node code publish ==")
    for entry in seed.get("node_codes") or []:
        workflow_key = entry["workflow_key"]
        node_key = entry["node_key"]
        workspace_ids = bound.get(workflow_key) or []
        if not workspace_ids:
            failures.append(f"{workflow_key}/{node_key}: no bound workspace, cannot publish")
            continue
        for workspace_id in workspace_ids:
            if workspace_id.startswith("<new:"):
                client.actions.append(
                    f"WOULD publish node code {workflow_key}/{node_key} "
                    f"on new workspace of {workflow_key}"
                )
                continue
            base = f"/api/workspaces/{workspace_id}/workflows/{workflow_key}/nodes/{node_key}/code"
            current = client.get(base, allow_404=True)
            if current is None:
                failures.append(
                    f"{workspace_id}/{workflow_key}/{node_key}: 404 "
                    "(no active revision or unknown node)"
                )
                continue
            if current.get("origin") == "custom" and current.get("code") == entry["code"]:
                print(
                    f"  {workspace_id}/{workflow_key}/{node_key}: skip "
                    f"(published v{current.get('version')} identical)"
                )
                continue
            origin = current.get("origin")
            client.mutate(
                "PUT",
                base,
                {"code": entry["code"], "change_note": entry.get("change_note")},
                dry_note=f"save draft (was origin={origin})",
            )
            result = client.mutate("POST", f"{base}/publish", dry_note="publish node code")
            print(
                f"  {workspace_id}/{workflow_key}/{node_key}: origin={origin} -> "
                f"published v{(result or {}).get('version')}"
            )


def step5_skills(client: Client, seed: dict[str, Any], failures: list[str]) -> None:
    print("== step 5: skill sources + relock ==")
    skills = seed.get("skills") or {}
    desired_sources = skills.get("sources") or {}
    if not desired_sources:
        print("  (seed has no skills section, skip)")
        return
    desired_lock = (skills.get("lock") or {}).get("skills") or {}
    view = client.get("/api/admin/skill-sources") or {}
    entries = {str(s.get("key")): s for s in (view.get("skills") or [])}
    need_relock = False
    for key, source in sorted(desired_sources.items()):
        entry = entries.get(key)
        if (
            entry is not None
            and entry.get("repo") == source.get("repo")
            and entry.get("ref") == source.get("ref")
        ):
            locked = desired_lock.get(key) or {}
            if entry.get("locked_commit") == locked.get("commit") and not entry.get("stale"):
                print(
                    f"  {key}: skip (ref {source.get('ref')} @ "
                    f"{entry.get('locked_commit', '')[:12]} locked)"
                )
                continue
            need_relock = True
            print(
                f"  {key}: ref matches but lock drifted "
                f"(locked={entry.get('locked_commit')} stale={entry.get('stale')})"
            )
            continue
        client.mutate(
            "PUT",
            f"/api/admin/skill-sources/{key}",
            {"repo": source["repo"], "ref": source["ref"]},
            dry_note=(
                f"upsert skill source (was {entry.get('ref') if entry else 'absent'}) "
                f"-> {source.get('ref')}"
            ),
        )
        need_relock = True
        print(f"  {key}: upsert ref -> {source.get('ref')}")
    if need_relock:
        client.mutate(
            "POST",
            "/api/admin/skill-sources/relock",
            timeout=180,
            dry_note="resolve refs to commits",
        )
        print("  relock: triggered" if not client.dry_run else "  relock: WOULD trigger")
    else:
        print("  relock: skip (lock already matches the seed)")


# ---------------------------------------------------------------------------
# verification report
# ---------------------------------------------------------------------------


def verify(client: Client, seed: dict[str, Any], bound: dict[str, list[str]]) -> list[str]:
    print("== step 6: verification ==")
    failures: list[str] = []

    def check(ok: bool, label: str, detail: str) -> None:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {detail}")
        if not ok:
            failures.append(f"{label}: {detail}")

    for workflow in seed["workflows"]:
        key = workflow["key"]
        workspace_ids = [w for w in (bound.get(key) or []) if not w.startswith("<new:")]
        if not workspace_ids:
            check(False, f"workflow {key}", "no bound workspace on target")
            continue
        for workspace_id in workspace_ids:
            label = f"workflow {key} @ {workspace_id}"
            active = client.get(
                f"/api/workspaces/{workspace_id}/workflow-revisions/active", allow_404=True
            )
            if active is None:
                check(False, label, "no active revision")
                continue
            payload = (active or {}).get("workflow") or {}
            sig_ok = _api_workflow_signature(payload) == _seed_workflow_signature(
                workflow["definition"]
            )
            check(sig_ok, label, f"active revision, definition matches={sig_ok}")

    for agent in seed.get("agents") or []:
        agent_id = agent["agent_id"]
        capability = str(agent["definition"].get("capability"))
        for workspace_id in _agent_target_workspaces(seed, bound, capability):
            if workspace_id.startswith("<new:"):
                continue
            label = f"agent {workspace_id}/{agent_id}"
            detail = client.get(
                f"/api/agent-definitions/{agent_id}",
                allow_404=True,
                params={"workspace_id": workspace_id},
            )
            published = (detail or {}).get("published")
            if published is None:
                check(False, label, "no published version")
                continue
            same = content_equal(published.get("definition"), agent["definition"])
            check(same, f"{label} v{published.get('version')}", f"matches={same}")

    for entry in seed.get("node_codes") or []:
        workflow_key, node_key = entry["workflow_key"], entry["node_key"]
        for workspace_id in bound.get(workflow_key) or []:
            if workspace_id.startswith("<new:"):
                continue
            base = f"/api/workspaces/{workspace_id}/workflows/{workflow_key}/nodes/{node_key}/code"
            current = client.get(base, allow_404=True) or {}
            ok = current.get("origin") == "custom" and current.get("code") == entry["code"]
            check(
                ok,
                f"node code {workspace_id}/{workflow_key}/{node_key}",
                f"origin={current.get('origin')} version={current.get('version')} matches={ok}",
            )

    skills = seed.get("skills") or {}
    desired_sources = skills.get("sources") or {}
    if desired_sources:
        view = client.get("/api/admin/skill-sources") or {}
        entries = {str(s.get("key")): s for s in (view.get("skills") or [])}
        desired_lock = (skills.get("lock") or {}).get("skills") or {}
        for key, source in sorted(desired_sources.items()):
            entry = entries.get(key)
            if entry is None:
                check(False, f"skill {key}", "missing on target")
                continue
            locked = desired_lock.get(key) or {}
            ok = (
                entry.get("repo") == source.get("repo")
                and entry.get("ref") == source.get("ref")
                and entry.get("locked_commit") == locked.get("commit")
                and not entry.get("stale")
            )
            check(
                ok,
                f"skill {key}",
                f"ref={entry.get('ref')} locked={str(entry.get('locked_commit'))[:12]} "
                f"expected={str(locked.get('commit'))[:12]} stale={entry.get('stale')}",
            )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", default=None, help="prompted interactively when omitted")
    parser.add_argument("--seed", type=Path, required=True, help="path to seed.json")
    parser.add_argument(
        "--workspace",
        action="append",
        default=[],
        help="workspace to create and bind: 'name=workflow_key[:entity]'; repeatable; "
        "default: none (only already-bound workspaces are targeted)",
    )
    parser.add_argument("--dry-run", action="store_true", help="read-only comparison + plan")
    args = parser.parse_args()

    seed = load_seed(args.seed)
    if seed.get("executors"):
        print(
            "note: the seed's legacy 'executors' section is ignored "
            "(executor concept retired in schema v47; code nodes run on the implicit code pool)"
        )
    password = args.password or getpass.getpass(f"password for {args.username}@{args.base_url}: ")
    client = Client(args.base_url, args.dry_run)
    user = client.login(args.username, password)
    print(f"target={client.base_url} user={user.get('username')} dry_run={client.dry_run}")

    failures: list[str] = []
    bound = step1_workspaces(client, seed, args.workspace, failures)
    step2_agents(client, seed, bound, failures)
    step3_first_revisions(client, seed, bound, failures)
    step4_node_codes(client, seed, bound, failures)
    step5_skills(client, seed, failures)
    failures += verify(client, seed, bound)

    if client.actions:
        print("== actions ==")
        for action in client.actions:
            print(f"  {action}")
    if client.dry_run:
        # dry-run FAILs are "would be fixed" current differences, not errors.
        print(
            f"== dry-run done: {len(client.actions)} planned actions, "
            f"{len(failures)} current differences/issues =="
        )
        return 0
    if failures:
        print(f"== import done with {len(failures)} unmet items ==")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("== import done: all checks pass ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
