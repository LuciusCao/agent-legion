import sqlite3

from server.app.db.migrations.helpers import add_column_if_missing
from server.app.db.migrations.runner import Migration


def _apply(conn: sqlite3.Connection) -> None:
    # Videos: identity, packaging, and interaction-review columns.
    add_column_if_missing(
        conn,
        "videos",
        "content_type",
        "alter table videos add column content_type text not null default 'knowledge'",
    )
    add_column_if_missing(
        conn,
        "videos",
        "external_id",
        "alter table videos add column external_id text not null default ''",
    )
    add_column_if_missing(
        conn,
        "videos",
        "knowledge_code",
        "alter table videos add column knowledge_code text not null default ''",
    )
    add_column_if_missing(
        conn,
        "videos",
        "question_id",
        "alter table videos add column question_id text not null default ''",
    )
    add_column_if_missing(
        conn,
        "videos",
        "source_uuid",
        "alter table videos add column source_uuid text not null default ''",
    )
    add_column_if_missing(
        conn,
        "videos",
        "packed",
        "alter table videos add column packed integer not null default 0",
    )
    add_column_if_missing(
        conn,
        "videos",
        "interaction_stats_json",
        "alter table videos add column interaction_stats_json text not null default ''",
    )
    add_column_if_missing(
        conn,
        "videos",
        "interaction_review_status",
        "alter table videos add column interaction_review_status text not null default ''",
    )

    # Workspaces: CMS/resources, entity, intake, description, legacy pipeline config.
    add_column_if_missing(
        conn,
        "workspaces",
        "cms_config_json",
        "alter table workspaces add column cms_config_json text not null default '{}'",
    )
    add_column_if_missing(
        conn,
        "workspaces",
        "resource_config_json",
        "alter table workspaces add column resource_config_json text not null default '{}'",
    )
    add_column_if_missing(
        conn,
        "workspaces",
        "default_entity",
        "alter table workspaces add column default_entity text not null default 'question'",
    )
    add_column_if_missing(
        conn,
        "workspaces",
        "intake_config_json",
        "alter table workspaces add column intake_config_json text not null default '{}'",
    )
    add_column_if_missing(
        conn,
        "workspaces",
        "description",
        "alter table workspaces add column description text not null default ''",
    )
    add_column_if_missing(
        conn,
        "workspaces",
        "pipeline_config_json",
        "alter table workspaces add column pipeline_config_json text not null default '{}'",
    )

    # Packages: metadata and locking.
    add_column_if_missing(
        conn,
        "packages",
        "video_count",
        "alter table packages add column video_count integer not null default 0",
    )
    add_column_if_missing(
        conn,
        "packages",
        "size_bytes",
        "alter table packages add column size_bytes integer not null default 0",
    )
    add_column_if_missing(
        conn, "packages", "name", "alter table packages add column name text not null default ''"
    )
    add_column_if_missing(
        conn,
        "packages",
        "locked",
        "alter table packages add column locked integer not null default 0",
    )

    # Workspace DAG batch/job/run columns added after the original tables.
    add_column_if_missing(
        conn,
        "job_batches",
        "workspace_id",
        "alter table job_batches add column workspace_id text not null default 'default'",
    )
    add_column_if_missing(
        conn,
        "jobs",
        "workspace_id",
        "alter table jobs add column workspace_id text not null default 'default'",
    )
    add_column_if_missing(
        conn, "jobs", "stem", "alter table jobs add column stem text not null default ''"
    )
    add_column_if_missing(
        conn,
        "node_runs",
        "run_dir",
        "alter table node_runs add column run_dir text not null default ''",
    )
    add_column_if_missing(
        conn,
        "node_runs",
        "session_dir",
        "alter table node_runs add column session_dir text not null default ''",
    )


MIGRATION = Migration(version=3, name="legacy_columns", apply=_apply)
