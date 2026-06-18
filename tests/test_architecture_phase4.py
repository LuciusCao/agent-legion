import pytest

from scripts.check_architecture import check_repository

_EMPTY_BUDGETS = '{"route_exemptions": [], "files": {}}'


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture(autouse=True)
def _budgets(tmp_path):
    write(tmp_path / "config/architecture/architecture-budgets.json", _EMPTY_BUDGETS)


def test_rejects_executor_response_field_typed_as_dict_any(tmp_path):
    write(
        tmp_path / "server/app/routes/executor_contracts.py",
        "from typing import Any\n"
        "from pydantic import BaseModel\n"
        "class WorkspaceExecutorConfigurationResponse(BaseModel):\n"
        "    allocations: dict[str, Any]\n",
    )

    errors = check_repository(tmp_path)

    assert any("executor response field" in error and "allocations" in error for error in errors)


def test_accepts_executor_response_field_typed_as_model_list(tmp_path):
    write(
        tmp_path / "server/app/routes/executor_contracts.py",
        "from pydantic import BaseModel\n"
        "class ExecutorAllocationResponse(BaseModel):\n"
        "    executor_id: str\n"
        "class WorkspaceExecutorConfigurationResponse(BaseModel):\n"
        "    allocations: list[ExecutorAllocationResponse]\n",
    )

    assert check_repository(tmp_path) == []


def test_rejects_settings_store_legacy_agent_import(tmp_path):
    write(
        tmp_path / "frontend/src/stores/settingStore.ts",
        "import { getWorkspaceAgents, setWorkspaceAgent } from '../api'\n"
        "export const useSettingStore = create(() => ({\n"
        "  load: () => getWorkspaceAgents('x'),\n"
        "  save: () => setWorkspaceAgent('x', 'pi', 1),\n"
        "}))\n",
    )

    errors = check_repository(tmp_path)

    assert any("legacy Agent assignment" in error for error in errors)


def test_rejects_workspace_save_calling_replace_executor_configuration(tmp_path):
    write(
        tmp_path / "server/app/services/workspace_configuration.py",
        "class WorkspaceConfigurationService:\n"
        "    def save(self):\n"
        "        self.job_db.replace_workspace_executor_configuration('w', [], [], [])\n",
    )

    errors = check_repository(tmp_path)

    assert any("outside the aggregate transaction" in error for error in errors)


def test_rejects_frontend_handwritten_executor_definition(tmp_path):
    write(
        tmp_path / "frontend/src/types.ts",
        "export type ExecutorDefinition = { id: string; kind: string }\n"
        "export type ExecutorAllocation = { executor_id: string; concurrency_limit: number }\n",
    )

    errors = check_repository(tmp_path)

    assert any(
        "handwritten duplicate" in error and "ExecutorDefinition" in error for error in errors
    )
    assert any(
        "handwritten duplicate" in error and "ExecutorAllocation" in error for error in errors
    )


def test_accepts_frontend_executor_types_derived_from_generated_api(tmp_path):
    write(
        tmp_path / "frontend/src/types.ts",
        "import type { components } from './generated/api'\n"
        "type ApiSchemas = components['schemas']\n"
        "export type ExecutorDefinition = ApiSchemas['ExecutorDefinitionResponse']\n"
        "export type ExecutorAllocation = ApiSchemas['ExecutorAllocationResponse']\n"
        "export type WorkspaceExecutorConfiguration = "
        "ApiSchemas['WorkspaceExecutorConfigurationResponse']\n"
        "export type ExecutorCatalogResponse = ApiSchemas['ExecutorCatalogResponse']\n",
    )

    assert check_repository(tmp_path) == []
