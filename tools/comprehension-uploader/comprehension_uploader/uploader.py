from __future__ import annotations

import json
import logging
import time
from typing import Any

import requests

from comprehension_uploader.api_client import ComprehensionAPIClient
from comprehension_uploader.config import Config
from comprehension_uploader.db import Database
from comprehension_uploader.fingerprint import compute_question_fingerprint
from comprehension_uploader.package_parser import UploadRecord

logger = logging.getLogger(__name__)

RETRYABLE_CODES = {10998}


class Uploader:
    def __init__(
        self,
        config: Config,
        db: Database,
        api: ComprehensionAPIClient,
    ) -> None:
        self.config = config
        self.db = db
        self.api = api
        self.workspace_id: str | None = None

    def upload_batch(
        self,
        records: list[UploadRecord],
        batch_id: str,
        workspace_id: str | None = None,
    ) -> None:
        self.workspace_id = workspace_id
        for record in records:
            self.upload_one(record, batch_id, workspace_id=workspace_id)

    def upload_one(
        self,
        record: UploadRecord,
        batch_id: str,
        workspace_id: str | None = None,
    ) -> None:
        if workspace_id is not None:
            self.workspace_id = workspace_id
        fingerprint = self._resolve_fingerprint(record)
        for attempt in range(self.config.max_retries + 1):
            try:
                response = self.api.add(record, fingerprint)
                code = response.get("code", 0)
                message = response.get("message", "")
                if code == 0:
                    self._record_add_success(record, batch_id, fingerprint, response)
                    return
                if code == 11051:
                    self._handle_duplicate(record, batch_id, fingerprint, response)
                    return
                if code in (10011, 11052):
                    self._record_failure(
                        record, batch_id, fingerprint, "add", code, message, response
                    )
                    return
                if code in RETRYABLE_CODES and attempt < self.config.max_retries:
                    time.sleep(2**attempt)
                    continue
                self._record_failure(record, batch_id, fingerprint, "add", code, message, response)
                return
            except requests.RequestException as exc:
                if attempt < self.config.max_retries:
                    time.sleep(2**attempt)
                    continue
                self._record_failure(
                    record,
                    batch_id,
                    fingerprint,
                    "add",
                    api_message=str(exc),
                )
                return

    def _resolve_fingerprint(self, record: UploadRecord) -> str:
        if record.stem is not None and record.options is not None:
            fingerprint = compute_question_fingerprint(record.stem, record.options)
            if fingerprint is not None:
                return fingerprint
        if record.fingerprint:
            logger.warning(
                "stem/options missing for %s, trusting provided fingerprint",
                record.question_id,
            )
            return record.fingerprint
        raise ValueError(
            f"Cannot compute fingerprint for {record.question_id}: "
            "missing stem/options and fingerprint"
        )

    def _handle_duplicate(
        self,
        record: UploadRecord,
        batch_id: str,
        fingerprint: str,
        response: dict[str, Any],
    ) -> None:
        if self.config.upload_on_duplicate == "skip":
            log_id = self._record_failure(
                record,
                batch_id,
                fingerprint,
                "skip",
                api_code=11051,
                status="skipped",
                api_message=response.get("message", ""),
                api_response=response,
            )
            self.db.states.upsert_state(
                record.question_id, fingerprint, log_id, workspace_id=self.workspace_id
            )
            return

        update_fields = self._build_update_fields(record)
        if not update_fields:
            log_id = self._record_failure(
                record,
                batch_id,
                fingerprint,
                "skip",
                status="skipped",
                api_message="no fields to update",
            )
            self.db.states.upsert_state(
                record.question_id, fingerprint, log_id, workspace_id=self.workspace_id
            )
            return

        try:
            update_response = self.api.update(record, fingerprint, update_fields)
            code = update_response.get("code", 0)
            message = update_response.get("message", "")
            if code == 0:
                log_id = self._record_success(
                    record,
                    batch_id,
                    fingerprint,
                    "update",
                    code,
                    message,
                    update_response,
                )
            else:
                log_id = self._record_failure(
                    record,
                    batch_id,
                    fingerprint,
                    "update",
                    code,
                    message,
                    update_response,
                )
        except requests.RequestException as exc:
            log_id = self._record_failure(
                record,
                batch_id,
                fingerprint,
                "update",
                api_message=str(exc),
            )
        self.db.states.upsert_state(
            record.question_id, fingerprint, log_id, workspace_id=self.workspace_id
        )

    def _build_update_fields(self, record: UploadRecord) -> dict[str, Any]:
        previous = self.db.logs.get_latest_success(record.question_id)
        fields: dict[str, Any] = {}

        if record.comprehension_difficulty is not None and (
            previous is None
            or previous["comprehension_difficulty"] != record.comprehension_difficulty
        ):
            fields["comprehension_difficulty"] = record.comprehension_difficulty

        if record.comprehension_data and (
            previous is None
            or previous["comprehension_data_hash"] != record.comprehension_data_hash
        ):
            fields["comprehension_data"] = record.comprehension_data

        if record.format_vno and (previous is None or previous["format_vno"] != record.format_vno):
            fields["format_vno"] = record.format_vno

        return fields

    def _record_add_success(
        self,
        record: UploadRecord,
        batch_id: str,
        fingerprint: str,
        response: dict[str, Any],
    ) -> None:
        log_id = self._record_success(
            record,
            batch_id,
            fingerprint,
            "add",
            response.get("code", 0),
            response.get("message", ""),
            response,
            uploaded_record_id=self._extract_uploaded_id(response),
        )
        self.db.states.upsert_state(
            record.question_id, fingerprint, log_id, workspace_id=self.workspace_id
        )

    def _record_success(
        self,
        record: UploadRecord,
        batch_id: str,
        fingerprint: str,
        action: str,
        api_code: int,
        api_message: str,
        api_response: dict[str, Any],
        uploaded_record_id: int | None = None,
    ) -> int:
        return self.db.logs.insert(
            workspace_id=self.workspace_id,
            batch_id=batch_id,
            question_id=record.question_id,
            fingerprint=fingerprint,
            subject_id=record.subject_id,
            question_uuid=record.question_uuid,
            question_vno=record.question_vno,
            format_vno=record.format_vno,
            comprehension_difficulty=record.comprehension_difficulty,
            comprehension_data_hash=record.comprehension_data_hash,
            action=action,
            status="success",
            api_code=api_code,
            api_message=api_message,
            api_response=json.dumps(api_response, ensure_ascii=False),
            uploaded_record_id=uploaded_record_id,
        )

    def _record_failure(
        self,
        record: UploadRecord,
        batch_id: str,
        fingerprint: str,
        action: str,
        api_code: int | None = None,
        api_message: str = "",
        api_response: dict[str, Any] | None = None,
        status: str = "failed",
    ) -> int:
        return self.db.logs.insert(
            workspace_id=self.workspace_id,
            batch_id=batch_id,
            question_id=record.question_id,
            fingerprint=fingerprint,
            subject_id=record.subject_id,
            question_uuid=record.question_uuid,
            question_vno=record.question_vno,
            format_vno=record.format_vno,
            comprehension_difficulty=record.comprehension_difficulty,
            comprehension_data_hash=record.comprehension_data_hash,
            action=action,
            status=status,
            api_code=api_code,
            api_message=api_message,
            api_response=json.dumps(api_response, ensure_ascii=False) if api_response else None,
        )

    @staticmethod
    def _extract_uploaded_id(response: dict[str, Any]) -> int | None:
        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        value = data.get("result")
        return value if isinstance(value, int) else None
