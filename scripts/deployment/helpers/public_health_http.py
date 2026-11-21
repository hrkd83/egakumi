"""Read public JSON with the platform trust store and redirects disabled.

Windows uses the installed PowerShell/.NET HTTPS transport. Request values
travel over stdin, and failures never include URL, headers or response content.
Other hosts retain urllib's verified TLS and existing proxy configuration.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .powershell_runtime import locate_powershell, powershell_environment

MAX_RESPONSE_BYTES = 64 * 1024
ERROR = "Public JSON request did not return trusted metadata"

_WINDOWS_HTTP = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$handler = $client = $request = $response = $null
try {
    $spec = [Console]::In.ReadToEnd() | ConvertFrom-Json -ErrorAction Stop
    $handler = [Net.Http.HttpClientHandler]::new()
    $handler.AllowAutoRedirect = $false
    $handler.UseCookies = $false
    $handler.UseDefaultCredentials = $false
    $client = [Net.Http.HttpClient]::new($handler)
    $client.Timeout = [TimeSpan]::FromSeconds($spec.timeoutSeconds)
    $client.MaxResponseContentBufferSize = $spec.maximumBytes
    $request = [Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::Get, $spec.url)
    [void]$request.Headers.TryAddWithoutValidation('Accept', $spec.accept)
    [void]$request.Headers.TryAddWithoutValidation('User-Agent', $spec.userAgent)
    # ResponseContentRead keeps header and body reads under the same timeout
    # and bounded content buffer. Certificate validation stays at .NET defaults.
    $response = $client.SendAsync($request).GetAwaiter().GetResult()
    $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
    $cacheValues = $null
    $cacheControl = ''
    if ($response.Headers.TryGetValues('Cache-Control', [ref]$cacheValues)) {
        $cacheControl = [string]::Join(',', $cacheValues)
    }
    $result = @{ statusCode = [int]$response.StatusCode; body = $body; cacheControl = $cacheControl }
    [Console]::Out.WriteLine(($result | ConvertTo-Json -Compress -Depth 4))
    exit 0
} catch {
    [Console]::Out.WriteLine('{"error":"PublicHttpTransportError"}')
    exit 1
} finally {
    if ($null -ne $response) { $response.Dispose() }
    if ($null -ne $request) { $request.Dispose() }
    if ($null -ne $client) { $client.Dispose() }
    elseif ($null -ne $handler) { $handler.Dispose() }
}
"""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _check_request(url, accept, user_agent, timeout):
    if (not isinstance(url, str) or not url or len(url) > 8192
            or any(ord(char) < 32 or ord(char) == 127 for char in url)):
        raise ValueError(ERROR)
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.fragment or parsed.port not in (None, 443)):
        raise ValueError(ERROR)
    if type(timeout) is not int or not 1 <= timeout <= 20:
        raise ValueError(ERROR)
    if any(not isinstance(value, str) or not value or len(value) > 512
           or any(ord(char) < 32 or ord(char) == 127 for char in value)
           for value in (accept, user_agent)):
        raise ValueError(ERROR)


def _windows(url, accept, user_agent, timeout):
    executable = locate_powershell()
    encoded = base64.b64encode(_WINDOWS_HTTP.encode('utf-16-le')).decode('ascii')
    request = {'url': url, 'accept': accept, 'userAgent': user_agent,
               'timeoutSeconds': timeout, 'maximumBytes': MAX_RESPONSE_BYTES}
    result = subprocess.run(
        [executable, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
        input=json.dumps(request, ensure_ascii=False), capture_output=True,
        encoding='utf-8', timeout=timeout + 10, check=False,
        env=powershell_environment(executable),
    )
    if result.returncode != 0 or len(result.stdout.encode('utf-8')) > 6 * MAX_RESPONSE_BYTES:
        raise ValueError(ERROR)
    value = json.loads(result.stdout)
    if (type(value) is not dict or set(value) != {'statusCode', 'body', 'cacheControl'}
            or type(value['statusCode']) is not int or not 100 <= value['statusCode'] <= 599
            or not isinstance(value['body'], str) or not isinstance(value['cacheControl'], str)
            or len(value['body'].encode('utf-8')) > MAX_RESPONSE_BYTES):
        raise ValueError(ERROR)
    return value['statusCode'], json.loads(value['body']), value['cacheControl']


def _urllib(url, accept, user_agent, timeout):
    opener = build_opener(NoRedirect())
    request = Request(url, headers={'Accept': accept, 'User-Agent': user_agent})
    with opener.open(request, timeout=timeout) as response:
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError(ERROR)
        status = response.status
        cache_control = response.headers.get('Cache-Control', '')
        if type(status) is not int or not isinstance(cache_control, str):
            raise ValueError(ERROR)
        return status, json.loads(body), cache_control


def read_public_json(url, *, accept, user_agent, timeout=20):
    """Return an integer status, Python-decoded JSON and Cache-Control text.

    The caller checks its own exact JSON and cache contract. PowerShell only
    carries the raw HTTP body; it never coerces JSON types or accepts a health.
    """
    try:
        _check_request(url, accept, user_agent, timeout)
        reader = _windows if os.name == 'nt' else _urllib
        return reader(url, accept, user_agent, timeout)
    except (OSError, ValueError, TypeError, RuntimeError, subprocess.SubprocessError):
        raise ValueError(ERROR) from None