"""Protected phase 1/2 API-only execution. No auto-replay of stopped runs."""
import os, sys
sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ['AZURE_LOGGING_ENABLE_LOG_FILE'] = 'false'
from pathlib import Path
ROOT = Path(os.environ['GW_PHASE12_SOURCE_ROOT']).resolve()
RUNTIME_ROOT = Path(os.environ['GW_PHASE12_RUNTIME_ROOT']).resolve()
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'apps/api-gateway/deploy'))
import argparse, copy, hashlib, json, re, subprocess, threading, time, uuid
from operations.common import OperationContext
from deployment.helpers.configuration import load_configuration_source
from deployment.helpers.private_permissions import check_private_permissions
from deployment.lifecycle import ArmTransport
from standard_apis import resolve_publication_configuration
from deployment.helpers.apim_authentication import read_managed_entra_authentication, validate_common_authentication_policy, validate_common_named_value, validate_entra_named_value
import api_publication, entra_publication
BICEP = Path(os.environ['BICEP_CLI'])


def load_failed_stage_evidence(ctx, directory, plan, before):
    """Load only an explicitly selected protected stopped run before any checkpoint."""
    folder = ctx.output_path(Path(directory))
    if not folder.is_relative_to(ctx.workspace.root / 'evidence'):
        raise ValueError('Failed-stage evidence must belong to the selected protected evidence directory')
    check_private_permissions(folder, directory=True)

    def read(name):
        path = ctx.output_path(folder / name)
        check_private_permissions(path)
        return json.loads(path.read_text(encoding='utf-8'))

    if (folder / 'staged-source-checkpoint.json').exists() or (folder / 'publication-result.json').exists():
        raise ValueError('Failed-stage evidence cannot follow a staged checkpoint or completed publication')
    failure = read('run-failure.json')
    api_path = plan['deploymentId'] + '?api-version=' + api_publication.DEPLOYMENT_API
    if (not isinstance(failure, dict) or failure.get('stage') != 'applying'
            or failure.get('lastMutationAttempt') != {'method': 'PUT', 'path': api_path}):
        raise ValueError('The selected stopped run is not a failed closed API stage')
    for name, raw in before.items():
        previous = read(name + '-before.json')
        current = json.loads(raw.decode('utf-8'))
        if json.dumps(previous, sort_keys=True, ensure_ascii=True) != json.dumps(current, sort_keys=True, ensure_ascii=True):
            raise ValueError('Protected input, Core state or checkpoint changed since the failed stage')
    hashes = folder / 'immutable-before-sha256.json'
    if hashes.exists():
        if read(hashes.name) != {name: hashlib.sha256(raw).hexdigest() for name, raw in before.items()}:
            raise ValueError('Protected input, Core state or checkpoint bytes changed since the failed stage')
    template = read('validated-template.json')
    intents = []
    for path in sorted(folder.glob('*-write-intent.json')):
        if not re.fullmatch(r'[0-9]{5,}-write-intent\.json', path.name):
            raise ValueError('Failed-stage write evidence has an unexpected filename')
        intent = read(path.name)
        if not isinstance(intent, dict) or set(intent) != {'method', 'path', 'body', 'headers'}:
            raise ValueError('Failed-stage write intent has an unexpected structure')
        if intent.get('method') == 'PUT' and intent.get('path') == api_path:
            intents.append((path, intent))
    if len(intents) != 1:
        raise ValueError('Failed-stage recovery requires exactly one recorded API publication PUT')
    path, intent = intents[0]
    receipt = read(path.name.replace('-write-intent.json', '-response.json'))
    generation_id = intent['body']['properties']['template']['outputs'][api_publication.SOURCE_GENERATION_OUTPUT]['value']
    api_publication.validate_source_generation_id(plan, generation_id)
    _, generation_template = api_publication.split_publication_templates(template)
    if generation_template is None:
        raise ValueError('Failed-stage evidence lacks its resource-free generation template')
    generation_template['outputs'][api_publication.SOURCE_GENERATION_OUTPUT] = {'type': 'string', 'value': generation_id}
    generation_template['outputs'][api_publication.SOURCE_PRINCIPAL_OUTPUT] = {
        'type': 'string', 'value': api_publication.SOURCE_PRINCIPAL_EXPRESSION}
    employee_paths = {plan['apimId'] + '/products/' + entra_publication.ENTRA_OPEN_PRODUCT_ID + '/apis/'
                      + resource_id.rsplit('/', 1)[-1] + '?api-version=' + entra_publication.APIM_API
                      for resource_id in plan['publishedApiIds'] if not resource_id.endswith('/credential-manager-browser')}
    for path in sorted(folder.glob('*-write-intent.json')):
        recorded = read(path.name)
        if recorded == intent:
            continue
        if (recorded.get('method') == 'DELETE' and recorded.get('path') in employee_paths
                and recorded.get('body') is None and recorded.get('headers') in (None, {})):
            continue
        if recorded.get('method') == 'PUT' and recorded.get('path') == generation_id + '?api-version=' + api_publication.DEPLOYMENT_API:
            body = recorded.get('body')
            properties = body.get('properties') if isinstance(body, dict) else None
            if (isinstance(body, dict) and set(body) == {'tags', 'properties'}
                    and body['tags'] == intent['body']['tags']
                    and isinstance(properties, dict) and set(properties) == {'mode', 'template', 'parameters'}
                    and recorded.get('headers') in (None, {}) and properties.get('mode') == 'Incremental' and
                    api_publication.canonical_publication_template(properties.get('template')) ==
                    api_publication.canonical_publication_template(generation_template) and
                    api_publication._recorded_parameters_match(properties.get('parameters'),
                                                              intent['body']['properties']['parameters'])):
                continue
        raise ValueError('Stopped-run evidence includes a mutation outside the recorded closed API stage')
    native_before = read('native-product-before.json')
    return {'template': template, 'intent': intent, 'response': receipt, 'nativeProductsBefore': native_before}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--recover-failed-stage-evidence', type=Path)
    parser.add_argument('--restore-recorded-mcp-tool', action='store_true')
    args = parser.parse_args()
    if args.restore_recorded_mcp_tool and args.recover_failed_stage_evidence is None:
        parser.error('--restore-recorded-mcp-tool requires --recover-failed-stage-evidence')
    ctx = OperationContext.load(Path(os.environ['GW_PHASE12_CONFIG_FILE']), runtime_root=RUNTIME_ROOT)
    run = ctx.output_path(ctx.workspace.root / 'evidence' / ('apim-phase12-' + uuid.uuid4().hex))
    active = ctx.output_path(ctx.workspace.state / 'apim-phase12-active.json')
    checkpoint_path = ctx.output_path(ctx.workspace.state / 'gateway-api-source.json')
    counters = {'requests': 0, 'mutationAttempts': 0, 'successfulMutationResponses': 0, 'readOnlyPosts': 0}
    stage = 'preparing'
    attempted = None
    staged_checkpoint = None
    lock = threading.Lock()
    last_report = [time.monotonic()]

    def save(name, value):
        return ctx.save_private(run / name, value)

    def status(value):
        nonlocal stage
        stage = value
        ctx.save_private(active, {'schemaVersion': 1, 'stage': stage, 'evidence': str(run), **counters}, overwrite=True)
        print(json.dumps({'stage': stage, **counters}), flush=True)

    class RecordedTransport:
        supports_concurrent_reads = True

        def __init__(self, config):
            self.inner = ArmTransport(config)

        def request(self, method, path, body=None, headers=None):
            nonlocal attempted
            readonly_post = method == 'POST' and path.split('?', 1)[0].rstrip('/').endswith('/exportTemplate')
            mutation = method != 'GET' and (not readonly_post)
            with lock:
                counters['requests'] += 1
                n = counters['requests']
                if readonly_post:
                    counters['readOnlyPosts'] += 1
                if mutation:
                    counters['mutationAttempts'] += 1
                    attempted = {'method': method, 'path': path}
            if mutation:
                save(f'{n:05d}-write-intent.json', {'method': method, 'path': path, 'body': body, 'headers': headers})
                status(stage)
            try:
                response = self.inner.request(method, path, body=body, headers=headers)
            except Exception as e:
                save(f'{n:05d}-request-failure.json', {'method': method, 'path': path, 'errorType': type(e).__name__, 'detail': str(e)})
                raise
            save(f'{n:05d}-response.json', {'method': method, 'path': path, 'response': response})
            with lock:
                if mutation and response.get('statusCode') in (200, 201, 202, 204):
                    counters['successfulMutationResponses'] += 1
                if time.monotonic() - last_report[0] > 30:
                    print(json.dumps({'stage': stage, **counters}), flush=True)
                    last_report[0] = time.monotonic()
            return response

    with ctx.locked():
        try:
            fresh = OperationContext.load(ctx.config_path, runtime_root=RUNTIME_ROOT)
            if fresh.config != ctx.config or fresh.state != ctx.state:
                raise ValueError('Protected configuration or Core state changed before lock acquisition')
            ctx = fresh
            if active.exists():
                check_private_permissions(active)
                save('previous-active-record.json', json.loads(active.read_text(encoding='utf-8')))
            before = {}
            for name, path in [('input', ctx.config_path), ('core-state', ctx.state_path), ('checkpoint', checkpoint_path)]:
                check_private_permissions(path)
                before[name] = path.read_bytes()
                save(name + '-before.json', json.loads(before[name].decode('utf-8')))
            save('immutable-before-sha256.json', {name: hashlib.sha256(raw).hexdigest() for name, raw in before.items()})
            source = load_configuration_source(ctx.config_path)
            state = copy.deepcopy(ctx.state)
            state['apiPublicationSourceCheckpoint'] = json.loads(before['checkpoint'].decode('utf-8'))
            config = resolve_publication_configuration(source, state)
            plan = api_publication.prepare_plan(config, state)
            save('validated-plan.json', plan)
            failed_stage_evidence = (None if args.recover_failed_stage_evidence is None else
                                    load_failed_stage_evidence(ctx, args.recover_failed_stage_evidence, plan, before))
            if failed_stage_evidence is not None:
                save('recorded-failed-stage-evidence.json', failed_stage_evidence)
            result = subprocess.run([str(BICEP), 'build', str(api_publication.MODULE), '--stdout'], capture_output=True, check=False, timeout=120)
            save('compiler-stderr.txt', result.stderr.decode('utf-8', errors='replace'))
            if result.returncode:
                raise ValueError('Official Bicep compilation failed')
            template = json.loads(result.stdout.decode('utf-8', errors='strict'))
            api_publication.validate_publication_template(template)
            count = sum((len(g['members']) for g in template.get('functions', [])))
            save('validated-template.json', template)
            status('prepared')
            print(json.dumps({'prepared': True, 'compiledUdfCount': count, 'apiCount': len(plan['publishedApiIds']), 'azureWrites': 0}), flush=True)
            if not args.apply:
                return 0
            transport = RecordedTransport(config)
            status('applying')

            def checkpoint(value):
                nonlocal staged_checkpoint
                staged_checkpoint = copy.deepcopy(value)
                save('staged-source-checkpoint.json', value)
                ctx.save_private(checkpoint_path, value, overwrite=True)
                status('staged-checkpoint-saved')

            publication = api_publication.execute_plan(config, state, plan, template, transport, checkpoint=checkpoint,
                                                       reconcile_existing_source=False,
                                                       failed_closed_stage_attempt=failed_stage_evidence,
                                                       restore_recorded_mcp_tool=args.restore_recorded_mcp_tool)
            save('publication-result.json', publication)
            status('published-readback')
            check_private_permissions(checkpoint_path)
            saved = json.loads(checkpoint_path.read_text(encoding='utf-8'))
            if staged_checkpoint is None or saved != staged_checkpoint:
                raise ValueError('The final checkpoint differs from the staged callback snapshot')
            expected = api_publication.source_checkpoint(plan, saved['source'], saved['globalPolicy'], entra_authentication=saved['entraAuthentication'], browser_source=saved.get('browserSource'), generation_id=saved.get('sourceGenerationId'))
            if saved != expected or saved['source'] != publication['entraPublicationSource']:
                raise ValueError('The protected current checkpoint differs from the applied source')
            names = {a['name'] for a in saved['source']['apis']}
            global_path = plan['apimId'] + '/policies/policy?api-version=' + api_publication.APIM_API + '&format=rawxml'
            global_response = transport.request('GET', global_path, headers={'Accept': 'application/json'})
            if global_response.get('statusCode') != 200:
                raise ValueError('Current Global readback failed')
            actual_global = global_response['body']['properties']['value']
            entra_publication._same_xml(actual_global, saved['globalPolicy'])
            fragments = api_publication._read_authentication_fragments(actual_global, plan['apimId'], transport)
            actual_auth = read_managed_entra_authentication(actual_global, plan.get('credentialLogin'), expected_entra=saved['entraAuthentication'], fragments=fragments)
            validate_common_authentication_policy(actual_global, plan.get('credentialLogin'), entra=saved['entraAuthentication'], fragments=fragments)
            if (actual_auth or {'enabled': False}) != saved['entraAuthentication']:
                raise ValueError('Current authentication differs from the checkpoint')
            for name, value in {**plan['commonAuthentication'], **saved['entraNamedValues']}.items():
                observed = transport.request('GET', plan['apimId'] + '/namedValues/' + name + '?api-version=' + api_publication.APIM_API)
                if observed.get('statusCode') != 200:
                    raise ValueError('Current authentication NamedValue readback failed')
                validator = validate_common_named_value if name in plan['commonAuthentication'] else validate_entra_named_value
                validator(observed['body'], name, value)
            manifest = entra_publication.source_manifest(saved['source'], plan['apimId'], names, saved['globalPolicy'])
            manifest = entra_publication.complete_named_value_expectations(manifest, plan, transport)
            snapshot = entra_publication.read_policy_graph(manifest, transport,
                native_product_before=None if failed_stage_evidence is None else failed_stage_evidence['nativeProductsBefore'])
            members_expected = {plan['apimId'] + '/apis/' + name for name in saved['entraAuthentication'].get('apiNames', [])}
            product_snapshot, members = entra_publication.read_open_product(plan['apimId'], transport, allowed_api_ids=members_expected, missing=False)
            if set(members) != members_expected:
                raise ValueError('Employee Product membership differs from the current source')
            snapshot.update(product_snapshot)
            if saved.get('browserSource'):
                snapshot.update(entra_publication.verify_browser_publication_source(plan, saved['browserSource'], transport))
            save('strict-readback-snapshot.json', {rid: {'path': p, 'headers': h, 'response': r} for rid, (p, h, r) in snapshot.items()})
            entra_publication.verify_snapshot(snapshot, transport)
            if ctx.config_path.read_bytes() != before['input'] or ctx.state_path.read_bytes() != before['core-state']:
                raise ValueError('Core state or input changed during API-only publication')
            save('strict-readback-result.json', {'checkpointCurrent': True, 'fullPolicyGraphVerified': True, 'exactOpenProductVerified': True, 'inputAndCoreStateUnchanged': True, 'runtimeVerified': False, **counters})
            status('management-plane-verified')
            return 0
        except Exception as e:
            save('run-failure.json', {'stage': stage, 'errorType': type(e).__name__, 'detail': str(e), 'lastMutationAttempt': attempted, **counters})
            status('stopped')
            print(json.dumps({'status': 'stopped', 'errorType': type(e).__name__, 'inspectPrivateEvidenceBeforeRetry': True}), flush=True)
            return 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as e:
        print(json.dumps({'status': 'preflight-stopped', 'errorType': type(e).__name__}), flush=True)
        raise SystemExit(1) from None
