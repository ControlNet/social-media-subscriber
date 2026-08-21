"""Descriptor-anchored promotion for complete snapshot directories."""

from __future__ import annotations

import shutil

from social_media_subscriber.storage.safe_directory import (
    DirectoryAnchor,
    FileIdentity,
    UnsafePathError,
)


def promote_directory(
    anchor: DirectoryAnchor,
    candidate: str,
    candidate_identity: FileIdentity,
    prior_identity: FileIdentity | None,
) -> None:
    """Promote a verified candidate without following or replacing unknown entries."""
    anchor.verify_parent_path()
    _require_identity(anchor, anchor.entry_name, prior_identity)
    _require_identity(anchor, candidate, candidate_identity)
    if prior_identity is None:
        _promote_without_prior(anchor, candidate, candidate_identity)
        return
    backup = anchor.make_directory(f".{anchor.entry_name}.previous.")
    anchor.remove_empty_directory(backup)
    anchor.rename(anchor.entry_name, backup)
    _require_identity(anchor, backup, prior_identity)
    if anchor.entry_identity() is not None:
        raise UnsafePathError
    try:
        _require_identity(anchor, candidate, candidate_identity)
        anchor.rename(candidate, anchor.entry_name)
    except BaseException:
        _restore_backup(anchor, backup, prior_identity)
        raise
    try:
        _require_identity(anchor, anchor.entry_name, candidate_identity)
        anchor.verify_parent_path()
        anchor.remove_tree(backup, expected=prior_identity)
    except BaseException:
        _rollback_candidate(
            anchor,
            candidate,
            candidate_identity,
            backup,
            prior_identity,
        )
        raise


def _promote_without_prior(
    anchor: DirectoryAnchor,
    candidate: str,
    candidate_identity: FileIdentity,
) -> None:
    if anchor.entry_identity() is not None:
        raise UnsafePathError
    anchor.rename(candidate, anchor.entry_name)
    try:
        _require_identity(anchor, anchor.entry_name, candidate_identity)
        anchor.verify_parent_path()
    except BaseException:
        if anchor.entry_identity() == candidate_identity:
            anchor.rename(anchor.entry_name, candidate)
        raise


def _rollback_candidate(
    anchor: DirectoryAnchor,
    candidate: str,
    candidate_identity: FileIdentity,
    backup: str,
    prior_identity: FileIdentity,
) -> None:
    _require_identity(anchor, anchor.entry_name, candidate_identity)
    anchor.rename(anchor.entry_name, candidate)
    _require_identity(anchor, candidate, candidate_identity)
    _restore_backup(anchor, backup, prior_identity)


def _restore_backup(
    anchor: DirectoryAnchor,
    backup: str,
    prior_identity: FileIdentity,
) -> None:
    if anchor.entry_identity() is not None:
        raise UnsafePathError
    _require_identity(anchor, backup, prior_identity)
    anchor.rename(backup, anchor.entry_name)
    _require_identity(anchor, anchor.entry_name, prior_identity)


def _require_identity(
    anchor: DirectoryAnchor,
    name: str,
    expected: FileIdentity | None,
) -> None:
    try:
        actual = anchor.entry_identity(name)
    except (OSError, shutil.Error) as error:
        raise UnsafePathError from error
    if actual != expected:
        raise UnsafePathError
