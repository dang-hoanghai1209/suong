"""Typed live executor for the R2-only reference transport canary.

All client and fetcher construction remains injected so tests stay fully local.
The CLI supplies concrete live-only factories after every authorization gate.
Importing this module performs no credential or network access.
"""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from typing import Any, Callable

from pydantic import SecretStr

from scripts.benchmarks.r2_reference_transport_canary import (
    R2ReferenceTransportCanaryConfig,
    deterministic_test_png,
    redact_presigned_url,
    validate_live_prerequisites,
)
from tella.media.r2_reference_store import (
    R2ReferenceStoreConfig,
    R2TemporaryReferenceStore,
    S3CompatibleClient,
)
from tella.media.temporary_reference_store import (
    ReferenceURLFetcher,
    TemporaryReferenceObject,
    upload_and_verify_reference,
    validate_image_bytes,
)


EXPECTED_TEST_PNG_SHA256 = (
    "99ac29d0e49ebcb6a8ed06859beb8d6d59c1c926198c2d66b1a940ac97db2ceb"
)
EXPECTED_TEST_PNG_BYTES = 414


class R2CanaryExecutionError(RuntimeError):
    """Safe failure with redacted structured diagnostics."""

    def __init__(self, category: str, diagnostic: dict[str, Any]) -> None:
        self.category = category
        self.diagnostic = diagnostic
        super().__init__(f"R2 transport canary failed: {category}")


async def execute_r2_transport_canary(
    config: R2ReferenceTransportCanaryConfig,
    *,
    mode: str,
    authorization_token: str,
    client_factory: Callable[[R2ReferenceStoreConfig], S3CompatibleClient],
    url_fetcher_factory: Callable[[], ReferenceURLFetcher],
) -> dict[str, Any]:
    """Run one bounded lifecycle using only injected R2 and HTTPS boundaries."""
    if mode != "live-r2":
        raise R2CanaryExecutionError("explicit_live_r2_mode_required", {})
    validate_live_prerequisites(config, authorization_token=authorization_token)

    content = deterministic_test_png(config.test_image)
    source_sha256 = hashlib.sha256(content).hexdigest()
    if source_sha256 != EXPECTED_TEST_PNG_SHA256:
        raise R2CanaryExecutionError("deterministic_source_hash_mismatch", {})
    if len(content) != EXPECTED_TEST_PNG_BYTES:
        raise R2CanaryExecutionError("deterministic_source_size_mismatch", {})
    validate_image_bytes(
        content,
        content_type="image/png",
        expected_size=EXPECTED_TEST_PNG_BYTES,
        expected_dimensions=(config.test_image.width, config.test_image.height),
        maximum_megapixels=1.0,
    )

    store_config = R2ReferenceStoreConfig.from_environment().model_copy(update={
        "private_bucket_status_confirmed": True,
        "conditional_write_support_confirmed": True,
    })
    construction_diagnostic = {
        "status": "failed",
        "source_sha256": source_sha256,
        "byte_size": len(content),
        "mime_type": "image/png",
        "dimensions": [config.test_image.width, config.test_image.height],
        "cleanup_outcome": "no_owned_object",
        "cleanup_required": False,
        "accounting": {"r2_client_constructions": 0},
    }
    try:
        client = client_factory(store_config)
    except Exception as exc:
        raise R2CanaryExecutionError(
            getattr(exc, "safe_category", "client_construction_failed"),
            construction_diagnostic,
        ) from None
    construction_diagnostic["accounting"]["r2_client_constructions"] = 1
    try:
        fetcher = url_fetcher_factory()
    except Exception:
        raise R2CanaryExecutionError(
            "verification_fetcher_construction_failed", construction_diagnostic
        ) from None
    store = R2TemporaryReferenceStore(store_config, client=client, url_fetcher=fetcher)
    accounting: dict[str, int] = {"r2_client_constructions": 1}
    diagnostic: dict[str, Any] = {
        "status": "running",
        "source_sha256": source_sha256,
        "byte_size": len(content),
        "mime_type": "image/png",
        "dimensions": [config.test_image.width, config.test_image.height],
        "bucket_identifier_sha256": hashlib.sha256(
            store_config.bucket_name.encode("utf-8")
        ).hexdigest(),
        "ttl_seconds": config.transport_policy.presigned_get_ttl_seconds,
        "conditional_write_observed_result": {},
        "cleanup_outcome": "not_started",
        "cleanup_required": False,
        "accounting": accounting,
    }
    owned: TemporaryReferenceObject | None = None
    failure_category: str | None = None
    cancelled = False

    try:
        try:
            owned = await upload_and_verify_reference(
                store,
                filename="tella-r2-reference-canary.png",
                content=content,
                content_type="image/png",
                approved_sha256=source_sha256,
                ttl_seconds=config.transport_policy.presigned_get_ttl_seconds,
                download_timeout_seconds=store_config.upload_timeout_seconds,
                expected_dimensions=(config.test_image.width, config.test_image.height),
                maximum_reference_bytes=config.test_image.maximum_bytes,
                maximum_reference_megapixels=1.0,
                accounting=accounting,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            failure_category = "initial_upload_or_roundtrip_failed"
        if owned is not None:
            safe_object = owned.diagnostic()
            diagnostic.update({
                "object_key_sha256": safe_object["object_key_sha256"],
                **redact_presigned_url(owned.read_url.get_secret_value()),
                "expires_at": safe_object["expires_at"],
                "roundtrip_sha256": owned.roundtrip_sha256,
            })

            second = await _conditional_write(
                client,
                store_config,
                object_key=owned.object_key,
                content=content,
                content_type="image/png",
                source_sha256=source_sha256,
                owner_token="r2-canary-identical-write",
                accounting=accounting,
            )
            diagnostic["conditional_write_observed_result"]["identical"] = second
            if second not in {"409", "412"}:
                failure_category = "identical_conditional_write_did_not_conflict"

            borrowed_ok = await _verify_identical_borrowed_object(
                client,
                store,
                store_config,
                owned,
                content=content,
                accounting=accounting,
            )
            diagnostic["borrowed_object_policy_verified"] = borrowed_ok
            if not borrowed_ok and failure_category is None:
                failure_category = "borrowed_object_policy_failed"

            conflicting = bytearray(content)
            conflicting[-13] ^= 0x01
            conflict_result = await _conditional_write(
                client,
                store_config,
                object_key=owned.object_key,
                content=bytes(conflicting),
                content_type="image/png",
                source_sha256=hashlib.sha256(conflicting).hexdigest(),
                owner_token="r2-canary-conflicting-write",
                accounting=accounting,
            )
            diagnostic["conditional_write_observed_result"]["conflicting"] = (
                conflict_result
            )
            if conflict_result not in {"409", "412"} and failure_category is None:
                failure_category = "conflicting_conditional_write_did_not_conflict"
    except asyncio.CancelledError:
        cancelled = True
    except R2CanaryExecutionError as exc:
        failure_category = exc.category
    except Exception:
        failure_category = "unexpected_executor_failure"
    finally:
        if owned is not None and owned.cleanup_owned:
            _inc(accounting, "reference_cleanup_attempts")
            try:
                deleted = await asyncio.shield(store.delete(owned))
            except Exception:
                deleted = False
            _inc(
                accounting,
                "reference_cleanup_successes" if deleted
                else "reference_cleanup_failures",
            )
            diagnostic["cleanup_outcome"] = "deleted" if deleted else "delete_failed"
            if deleted:
                _inc(accounting, "post_cleanup_absence_checks")
                try:
                    absent = not await store.exists(owned.object_key)
                except Exception:
                    absent = False
                diagnostic["post_cleanup_absence_confirmed"] = absent
                if not absent:
                    diagnostic["cleanup_required"] = True
                    if failure_category is None:
                        failure_category = "post_cleanup_absence_not_confirmed"
            else:
                diagnostic["cleanup_required"] = True
                if failure_category is None:
                    failure_category = "owned_object_cleanup_failed"
        else:
            helper_cleanup_failures = accounting.get("reference_cleanup_failures", 0)
            helper_cleanup_successes = accounting.get("reference_cleanup_successes", 0)
            if helper_cleanup_failures:
                diagnostic["cleanup_outcome"] = "helper_cleanup_failed"
                diagnostic["cleanup_required"] = True
            elif helper_cleanup_successes:
                diagnostic["cleanup_outcome"] = "helper_cleanup_succeeded"
            else:
                diagnostic["cleanup_outcome"] = "no_owned_object"
        diagnostic["accounting"] = dict(accounting)
        _validate_budget(config, accounting)

    if cancelled:
        raise asyncio.CancelledError()
    if failure_category is not None:
        diagnostic["status"] = "failed"
        raise R2CanaryExecutionError(failure_category, diagnostic) from None
    diagnostic["status"] = "passed"
    return diagnostic


async def _conditional_write(
    client: S3CompatibleClient,
    config: R2ReferenceStoreConfig,
    *,
    object_key: str,
    content: bytes,
    content_type: str,
    source_sha256: str,
    owner_token: str,
    accounting: dict[str, int],
) -> str:
    _inc(accounting, "conditional_write_attempts")
    try:
        await asyncio.wait_for(
            asyncio.to_thread(
                client.put_object,
                Bucket=config.bucket_name,
                Key=object_key,
                Body=content,
                ContentType=content_type,
                Metadata={
                    "source-sha256": source_sha256,
                    "tella-upload-owner": owner_token,
                },
                IfNoneMatch="*",
            ),
            timeout=config.upload_timeout_seconds,
        )
        return "success"
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        status = _status_code(exc)
        if status in {409, 412}:
            return str(status)
        return "other_error"


async def _verify_identical_borrowed_object(
    client: S3CompatibleClient,
    store: R2TemporaryReferenceStore,
    config: R2ReferenceStoreConfig,
    owned: TemporaryReferenceObject,
    *,
    content: bytes,
    accounting: dict[str, int],
) -> bool:
    _inc(accounting, "borrowed_object_verifications")
    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(
                client.get_object,
                Bucket=config.bucket_name,
                Key=owned.object_key,
            ),
            timeout=config.upload_timeout_seconds,
        )
        body = _bounded_body(response.get("Body"), maximum_bytes=len(content))
        if body != content or response.get("ContentType") != "image/png":
            return False
        borrowed = replace(
            owned,
            read_url=SecretStr("redacted"),
            cleanup_owned=False,
        )
        return await store.delete(borrowed)
    except asyncio.CancelledError:
        raise
    except Exception:
        return False


def _bounded_body(body: Any, *, maximum_bytes: int) -> bytes:
    if isinstance(body, bytes):
        content = body[: maximum_bytes + 1]
    elif hasattr(body, "read"):
        content = bytes(body.read(maximum_bytes + 1))
    else:
        raise RuntimeError("R2 canary received an unreadable object body")
    if len(content) > maximum_bytes:
        raise RuntimeError("R2 canary object body exceeds byte limit")
    return content


def _status_code(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        metadata = response.get("ResponseMetadata", {})
        return metadata.get("HTTPStatusCode")
    return getattr(exc, "status_code", None)


def _inc(accounting: dict[str, int], key: str) -> None:
    accounting[key] = int(accounting.get(key, 0)) + 1


def _validate_budget(
    config: R2ReferenceTransportCanaryConfig, accounting: dict[str, int]
) -> None:
    if accounting.get("r2_client_constructions", 0) > 1:
        raise R2CanaryExecutionError("client_construction_budget_exceeded", {})
    total_writes = (
        accounting.get("temporary_store_upload_attempts", 0)
        + accounting.get("conditional_write_attempts", 0)
    )
    if total_writes > config.request_budget.immutable_upload_attempts_max:
        raise R2CanaryExecutionError("immutable_write_budget_exceeded", {})
    if (
        accounting.get("temporary_store_verification_downloads", 0)
        > config.request_budget.verification_downloads_max
    ):
        raise R2CanaryExecutionError("verification_download_budget_exceeded", {})
    if (
        accounting.get("temporary_store_presign_operations", 0)
        > config.request_budget.presign_operations_max
    ):
        raise R2CanaryExecutionError("presign_budget_exceeded", {})
    if accounting.get("reference_cleanup_attempts", 0) > (
        config.request_budget.cleanup_attempts_max
    ):
        raise R2CanaryExecutionError("cleanup_budget_exceeded", {})


__all__ = [
    "EXPECTED_TEST_PNG_BYTES",
    "EXPECTED_TEST_PNG_SHA256",
    "R2CanaryExecutionError",
    "execute_r2_transport_canary",
]
