#!/usr/bin/env python3
"""完成したCore基盤へGateway→Portalを配備する。既定は計画、--executeで実行。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from deployment.helpers.configuration import load_configuration
from deployment.helpers.paths import REPOSITORY, configuration_path, local_paths


class ApplicationDeploymentError(ValueError):
    """入力値や子プロセスの秘密出力を含めず、続行できない段階を示す。"""


def _repository_path(path: Path) -> Path:
    expanded = path.expanduser()
    return (expanded if expanded.is_absolute() else REPOSITORY / expanded).resolve()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path,
                        default=None)
    parser.add_argument('--state-file', type=Path)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    try:
        source = _repository_path(args.config or configuration_path(REPOSITORY))
        config = load_configuration(source)
        workspace = local_paths(config, REPOSITORY)
        workspace.checked(source)
        workspace.check_selection(config)
        state_file = workspace.checked(args.state_file or workspace.state / 'latest.json')
        state = json.loads(state_file.read_text(encoding='utf-8'))
        if not isinstance(state, dict) or any(state.get(key) != config[key]
                for key in ('subscriptionId', 'azureTenantId', 'resourceStem')):
            raise ApplicationDeploymentError('Core state differs from the selected configuration; finish Core provisioning first.')
        if args.execute and config.get('publishApis') is not True:
            raise ApplicationDeploymentError('Set publishApis=true for the Gateway and Portal installation.')
        mode = '配備' if args.execute else '計画'
        for name, filename in (('Gateway', 'deploy_gateway.py'), ('Portal', 'deploy_portal.py')):
            print(f'{name}: {mode}を開始します。', flush=True)
            command = [sys.executable, str(REPOSITORY / 'scripts' / filename),
                       '--config', str(source), '--state-file', str(state_file)]
            if args.execute:
                command.append('--execute')
            # 既存CLIの進捗・対話入力をそのまま端末へ接続する。結果JSONや成功記録を
            # 推測で読み替えず、今回の終了コードだけで次の段階へ進む。
            result = subprocess.run(command, cwd=REPOSITORY, shell=False, check=False)
            if result.returncode:
                print(f'{name} deployment stopped; inspect this stage before retrying.', file=sys.stderr)
                return result.returncode if result.returncode > 0 else 1
        print(f'GatewayとPortalの{mode}が完了しました。', flush=True)
        return 0
    except ApplicationDeploymentError as error:
        print('Application deployment stopped: ' + str(error), file=sys.stderr)
        return 1
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError):
        print('Application deployment stopped: review the selected configuration and completed Core state.', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('Application deployment cancelled; saved deployment state is retained.', file=sys.stderr)
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
