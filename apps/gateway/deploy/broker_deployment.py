"""Gateway配備で使うSQL初期化ジョブとBrokerの起動完了を確認する。"""
from __future__ import annotations

import re
import time

from deployment.helpers.credential_image import checked
from deployment.helpers.credential_pools import resource_names

APP_API = "2025-01-01"


def wait_for_schema(config, execution_name, transport, *, timeout=900, sleep=time.sleep, monotonic=time.monotonic, progress=print):
    job = group_scope(config) + '/providers/Microsoft.App/jobs/' + resource_names(config)['schemaJob']
    if not isinstance(execution_name, str) or not re.fullmatch(r'[A-Za-z0-9-]+', execution_name):
        raise ValueError('SQL schema execution name is invalid')
    deadline = monotonic() + timeout
    notice = monotonic() + 60
    while monotonic() < deadline:
        current = checked(transport, 'GET', job + '/executions/' + execution_name + '?api-version=' + APP_API,
                          allowed=(200, 404), label='Read SQL schema execution')
        status = current.get('properties', {}).get('status')
        if status == "Succeeded":
            return
        if status in ("Failed", "Stopped", "Degraded"):
            raise ValueError("SQL schema job failed; the application was not updated")
        if monotonic() >= notice:
            progress('認証窓口: SQL初期化の完了を待っています。', flush=True)
            notice = monotonic() + 60
        sleep(10)
    raise ValueError("SQL schema job did not complete within the wait limit")


def group_scope(config):
    """今回の配備先となるサービス用リソースグループのIDを返す。"""
    return '/subscriptions/' + config['subscriptionId'] + '/resourceGroups/' + config['resourceGroupNames']['svc']


def wait_for_application(config, transport, *, sleep=time.sleep, monotonic=time.monotonic, timeout=900, progress=print):
    target = group_scope(config) + '/providers/Microsoft.App/containerApps/' + resource_names(config)['application']
    deadline = monotonic() + timeout
    notice = monotonic() + 60
    while monotonic() < deadline:
        body = checked(transport, 'GET', target + '?api-version=' + APP_API, label='Read broker readiness')
        properties = body.get('properties', {})
        revision = properties.get('latestRevisionName')
        if revision and properties.get('latestReadyRevisionName') == revision:
            ready = checked(transport, 'GET', target + '/revisions/' + revision + '?api-version=' + APP_API,
                            label='Read broker revision health').get('properties', {})
            if (ready.get('healthState') == 'Healthy' and ready.get('provisioningState') == 'Provisioned'
                    and ready.get('runningState') == 'Running'):
                return
        if properties.get('provisioningState') == 'Failed':
            raise ValueError('Credential broker application failed to provision')
        if monotonic() >= notice:
            progress('認証窓口: 新しいリビジョンの正常起動を待っています。', flush=True)
            notice = monotonic() + 60
        sleep(10)
    raise ValueError('Credential broker revision is not ready; inspect Container Apps before publishing APIs')
