"""Create ordered immutable evidence records and obtain broker acknowledgement."""
import asyncio
from datetime import datetime, timezone
import json
from uuid import uuid4

from azure.eventhub import EventData, TransportType
from azure.eventhub.aio import EventHubProducerClient


MAX_EVENT_BYTES = 64 * 1024
PART_CHARACTERS = 8 * 1024
RECORD_KINDS = {
    'request', 'response', 'stream-start', 'stream-segment', 'stream-end', 'failure',
}


class AuditUnavailable(RuntimeError):
    pass


class EvidenceLimit(ValueError):
    pass


def json_bytes(value):
    return json.dumps(value, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


class Publisher:
    def __init__(self, settings, credential, timeout):
        self.timeout = timeout
        self.client = EventHubProducerClient(fully_qualified_namespace=settings.namespace,
            eventhub_name=settings.name, credential=credential, buffered_mode=False,
            transport_type=TransportType.AmqpOverWebsocket, retry_total=1, retry_backoff_factor=0.25)

    async def warm_up(self):
        async with asyncio.timeout(self.timeout):
            await self.client.create_batch()

    async def close(self):
        await self.client.close()

    async def publish(self, request_id, records):
        try:
            async with asyncio.timeout(self.timeout):
                batch = await self.client.create_batch(partition_key=request_id)
                for record in records:
                    event = EventData(record)
                    try:
                        batch.add(event)
                    except ValueError:
                        if not len(batch):
                            raise
                        await self.client.send_batch(batch)
                        batch = await self.client.create_batch(partition_key=request_id)
                        batch.add(event)
                if len(batch):
                    await self.client.send_batch(batch)
        except Exception:
            raise AuditUnavailable('Could not acknowledge audit records') from None


class Capture:
    def __init__(self, request_id, operation, publisher, max_capture_bytes, *, capture_id=None, sequence=0):
        self.request_id = request_id
        self.operation = operation
        self.publisher = publisher
        self.max_capture_bytes = max_capture_bytes
        self.capture_id = capture_id or str(uuid4())
        self.sequence = sequence

    async def save(self, kind, document):
        payload = json_bytes(document).decode('ascii')
        if len(payload) > self.max_capture_bytes:
            raise EvidenceLimit('Projected evidence exceeds limit')
        timestamp = datetime.now(timezone.utc).isoformat(timespec='milliseconds')
        chunks = [payload[index:index + PART_CHARACTERS] for index in range(0, len(payload), PART_CHARACTERS)]
        records = []
        for part, chunk in enumerate(chunks):
            record = json_bytes({
                'version': 1, 'requestId': self.request_id, 'captureId': self.capture_id,
                'operationId': self.operation, 'sequence': self.sequence, 'timeUtc': timestamp,
                'kind': kind, 'part': part, 'parts': len(chunks), 'data': chunk,
            })
            if len(record) > MAX_EVENT_BYTES:
                raise EvidenceLimit('Audit event exceeds limit')
            records.append(record)
            self.sequence += 1
        await self.publisher.publish(self.request_id, records)
