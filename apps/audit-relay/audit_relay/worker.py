"""Archive Event Hubs records before advancing the partition checkpoint."""
import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
import json
import logging
import re
from time import monotonic
from uuid import UUID

from azure.core.exceptions import ResourceExistsError
from azure.eventhub import TransportType
from azure.eventhub.aio import EventHubConsumerClient
from azure.eventhub.extensions.checkpointstoreblobaio import BlobCheckpointStore
from azure.identity.aio import ManagedIdentityCredential
from azure.storage.blob import ContentSettings
from azure.storage.blob.aio import ContainerClient
from fastapi import FastAPI
from fastapi.responses import Response

from .evidence import MAX_EVENT_BYTES, RECORD_KINDS, json_bytes
from .settings import WorkerSettings, valid_evidence_operation


LOGGER = logging.getLogger('audit-worker')
_EVENT_FIELDS = {'version', 'requestId', 'captureId', 'operationId', 'sequence', 'timeUtc',
                 'kind', 'part', 'parts', 'data'}


def valid_id(value):
    return isinstance(value, str) and str(UUID(value)) == value


def read_record(event):
    value = event.body_as_str(encoding='UTF-8')
    data = value.encode('utf-8')
    if len(data) > MAX_EVENT_BYTES:
        raise ValueError('Audit event is too large')
    record = json.loads(value)
    if (not isinstance(record, dict) or set(record) != _EVENT_FIELDS or record['version'] != 1
            or not valid_evidence_operation(record['operationId']) or record['kind'] not in RECORD_KINDS
            or not valid_id(record['requestId']) or not valid_id(record['captureId'])
            or not (type(record['sequence']) is int and 0 <= record['sequence'] < 10**9)
            or not (type(record['parts']) is int and type(record['part']) is int and 0 <= record['part'] < record['parts'] < 10**4)
            or not isinstance(record['timeUtc'], str) or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}\+00:00', record['timeUtc'])
            or not isinstance(record['data'], str) or not record['data'].isascii()):
        raise ValueError('Invalid audit envelope')
    return record, data


class ArchiveWorker:
    def __init__(self, settings, client, checkpoint, archive):
        self.settings = settings
        self.client = client
        self.checkpoint = checkpoint
        self.archive = archive
        self.started = False
        self.issues = set()
        self.last_progress = {}

    @property
    def ready(self):
        return self.started and not self.issues

    def progress(self, partition, event):
        now = monotonic()
        if now - self.last_progress.get(partition.partition_id, -30) < 30:
            return
        properties = getattr(partition, 'last_enqueued_event_properties', None) or {}
        last = properties.get('sequence_number')
        enqueued_at = getattr(event, 'enqueued_time', None)
        if isinstance(last, int) and isinstance(enqueued_at, datetime) and enqueued_at.tzinfo:
            self.last_progress[partition.partition_id] = now
            lag = max(0, last - event.sequence_number)
            age = max(0, int((datetime.now(timezone.utc) - enqueued_at).total_seconds()))
            LOGGER.warning('audit_worker_progress partition=%s lagEvents=%d recordAgeSeconds=%d',
                           partition.partition_id, lag, age)

    async def process(self, partition, event):
        try:
            record, payload = read_record(event)
            name = f'{record["requestId"]}/{record["captureId"]}/{record["sequence"]:09d}.json'
        except (ValueError, TypeError, UnicodeError, RecursionError):
            sequence = int(event.sequence_number)
            partition_id = int(partition.partition_id)
            name = f'rejected/{partition_id}/{sequence}.json'
            payload = json_bytes({'state': 'invalid-envelope', 'partition': partition_id, 'sequence': sequence,
                                  'timeUtc': datetime.now(timezone.utc).isoformat(timespec='milliseconds')})
            LOGGER.warning('audit_invalid_envelope partition=%d sequence=%d', partition_id, sequence)

        delay = 1
        while True:
            try:
                try:
                    async with asyncio.timeout(15):
                        await self.archive.upload_blob(name=name, data=payload, overwrite=False,
                            content_settings=ContentSettings(content_type='application/json'))
                except ResourceExistsError as error:
                    # The SDK also maps unrelated conflicts to this type. Use the original service code.
                    response = error.response
                    if response is None or (response.status_code, response.headers.get('x-ms-error-code')) not in {
                            (409, 'BlobAlreadyExists'), (412, 'ConditionNotMet')}:
                        raise
                    # The only write precondition for this blob is If-None-Match: *.
                async with asyncio.timeout(15):
                    await partition.update_checkpoint(event)
                self.issues.discard(('archive', partition.partition_id))
                self.progress(partition, event)
                return
            except Exception:
                # Don't let the SDK advance to another event from this partition before this one is archived.
                self.issues.add(('archive', partition.partition_id))
                LOGGER.warning('audit_worker_archive_retry')
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)

    async def error(self, partition, _error):
        self.issues.add(('receive', partition.partition_id if partition is not None else None))
        LOGGER.warning('audit_worker_receive_retry')

    async def initialized(self, partition):
        self.issues.discard(('receive', partition.partition_id))
        self.issues.discard(('receive', None))

    async def closed(self, partition, _reason):
        self.issues.discard(('archive', partition.partition_id))
        self.issues.discard(('receive', partition.partition_id))
        self.last_progress.pop(partition.partition_id, None)

    async def run(self):
        while True:
            try:
                async with asyncio.timeout(10):
                    await self.client.get_eventhub_properties()
                    await self.checkpoint.list_checkpoints(self.settings.hub.namespace, self.settings.hub.name,
                                                           self.settings.consumer_group)
                self.issues.clear()
                self.started = True
                await self.client.receive(on_event=self.process, on_error=self.error,
                    on_partition_initialize=self.initialized, on_partition_close=self.closed,
                    starting_position='-1', prefetch=100, track_last_enqueued_event_properties=True)
            except Exception:
                LOGGER.warning('audit_worker_start_retry')
            finally:
                self.started = False
            await asyncio.sleep(5)


def create_worker_app(settings=None):
    settings = settings or WorkerSettings.load()

    @asynccontextmanager
    async def lifespan(app):
        async with ManagedIdentityCredential(client_id=settings.hub.client_id) as credential:
            checkpoint = BlobCheckpointStore(settings.checkpoint_url, settings.checkpoint_container, credential=credential)
            consumer = EventHubConsumerClient(fully_qualified_namespace=settings.hub.namespace,
                eventhub_name=settings.hub.name, consumer_group=settings.consumer_group, credential=credential,
                checkpoint_store=checkpoint, transport_type=TransportType.AmqpOverWebsocket)
            archive = ContainerClient(account_url=settings.evidence_url, container_name=settings.evidence_container,
                credential=credential, max_single_put_size=128 * 1024, retry_total=1, connection_timeout=4, read_timeout=6)
            try:
                async with consumer, archive:
                    app.state.worker = ArchiveWorker(settings, consumer, checkpoint, archive)
                    task = asyncio.create_task(app.state.worker.run())
                    try:
                        yield
                    finally:
                        task.cancel()
                        with suppress(asyncio.CancelledError):
                            await task
            finally:
                await checkpoint.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get('/health')
    async def health():
        return Response(status_code=200)

    @app.get('/ready')
    async def ready():
        return Response(status_code=200 if app.state.worker.ready else 503)

    return app
