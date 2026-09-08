"""Trusted, local bridge from existing Root Writer receipts to broker storage.

Workspace/profile/request come from the service, never arbitrary worker paths.
This module does not write content, authenticate clients, or launch workers.
"""
import json

from pipeline import root_writer as writer
from pipeline.task_broker import BrokerError


def accept_writer_result(broker, *, workspace, profile_path, project, task,
                         agent, token, version, request):
    # Snapshot caller-owned input before validation; never reread a worker file.
    request = json.loads(writer._canonical_json(request))
    workspace = writer._validate_workspace(workspace)
    profile = writer.load_project_profile(profile_path)
    writer._validate_profile_package(workspace, profile_path, profile)
    writer._validate_request_header(request, profile)
    if profile['project_id'] != project or request['run_id'] != task:
        raise BrokerError('Writer result belongs to another project or task')
    # Lock ordering: Writer lock, then broker transaction; no inverse callers.
    with writer._workspace_lock(workspace):
        prepared = writer.validate_request(request, profile, workspace, allow_existing_exact=True)
        with writer._project_state_fd(workspace, profile['_state_root']) as state_fd:
            receipt = writer._idempotent_receipt(
                writer._receipt_relative(profile, request),
                writer._sha256(writer._canonical_json(request)), state_fd, request)
            if receipt is None:
                raise BrokerError('Completed Writer receipt is missing')
            expected = [dict(path=item['relative'].as_posix(), mode='create',
                             sha256=item['content_sha256'], bytes=len(item['content']),
                             media_type=item['media_type']) for item in prepared]
            if receipt['writes'] != expected:
                raise BrokerError('Writer receipt does not match requested output set')
            assets = [dict(asset_id=item['path'], revision=request['idempotency_key'],
                           sha256=item['sha256'], artifact_ref=item['path'],
                           media_type=item['media_type'], byte_size=item['bytes'])
                      for item in receipt['writes']]
            return broker.accept_verified_result(project, task, agent=agent,
                                                 token=token, version=version, assets=assets)


def reconcile_review(broker, review_service, *, project, task):
    """Mirror a receipted Review decision; never create one or initiate publishing.

    The service and broker are trusted and must address the same project workspace.
    ReviewService verifies artifact bytes plus the action's completed Writer receipt.
    An interrupted mirror is safe to retry; the original decision stays authoritative.
    """
    record = broker.get(project, task)
    items = review_service.reviews(project)['reviews']
    candidates = [item for item in items if item['artifact_id'] == task]
    if len(candidates) != 1:
        raise BrokerError('Task review item is unavailable or ambiguous')
    selected = candidates[0]
    action = selected['action']
    if action is None:
        return record
    targets = broker.db.execute('''SELECT asset_id, revision FROM task_assets
        WHERE project_id=? AND task_id=? AND artifact_ref=? AND sha256=?''',
        (project, task, selected['revision']['artifact_path'], selected['artifact_sha256'])).fetchall()
    if len(targets) != 1:
        raise BrokerError('Reviewed bytes do not match one accepted task asset')
    source_ref = review_service._action_relative(selected['artifact_id'], selected['artifact_revision']).as_posix()
    return broker.accept_verified_decision(project, task, decision_id=action['action_id'],
        asset_id=targets[0]['asset_id'], revision=targets[0]['revision'],
        sha256=selected['artifact_sha256'], action=action['action'], note_ref=source_ref,
        source_sha256=writer._sha256(writer._canonical_json(action)))
