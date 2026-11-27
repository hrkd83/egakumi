# 管理VMに保守担当者本人で接続する

管理VMはプライベートIPで稼働し、LinuxのMicrosoft Entra SSH拡張とシステム割り当てマネージドIDで本人のSSHログインを認可します。構築時の入力に担当者のロール割り当てはありません。運用の標準は、保守期間中に承認された資源への権限を担当者本人へ手動で付与し、終了後に回収する方式です。組織がPIMを採用している場合は、期限付き割り当ての運用に使えます。

## 事前確認

- 保守申請で、対象のテナント・サブスクリプション・管理VM、担当者本人の対象テナント内Object ID、開始・終了予定、使用する接続経路、必要な操作と対象資源を照合します。付与と回収の担当者を決め、記録は保護領域で管理します。
- 配置担当者へ対象VMのリソースIDとプライベートIPを確認します。管理VMのAzure公開IPはありません。組織承認済みのVPNや接続済みハブから、管理VMのプライベートIPへの往復経路を確保します。この資材はVPNやBastionを作成しないため、空環境では到達経路を用意してから接続試験を行います。
- 初期NSGに入方向SSH許可はありません。承認された経路で必要になった場合は、ネットワーク担当が管理VMサブネットのNSGをAzureポータルで開き、受信セキュリティ規則を追加します。送信元はそのNSGから見える承認済みCIDR、宛先は対象VMのプライベートIPの`/32`、プロトコルと宛先ポートはTCP 22、アクションはAllow、優先度は既存の規則と重複せず終端拒否より前を指定します。利用するFirewallやハブにも必要な場合は、同じ範囲で経路と許可を用意します。構築の再適用やNSG標準化との競合を避け、変更した規則を保護記録に控えます。
- Azure CLIとOpenSSHを接続元に用意し、`az extension add --name ssh --upgrade`でSSH拡張を準備します。ゲストの`AADSSHLoginForLinux`拡張が正常であることを確認します。VMからは、システム割り当てIDのIMDS、Azure CLI認証とRBAC照合のMicrosoft Entraエンドポイント、SSH拡張のパッケージ取得先へ到達できる必要があります。既存のEntraサービスタグとパッケージ宛て許可の実効性を新規環境で確認します。 [Entra SSHの前提と条件付きアクセス](https://learn.microsoft.com/en-us/entra/identity/devices/howto-vm-sign-in-azure-ad-linux)
- 既設Bastionを選ぶ場合は、BastionからSSHが届く構成を用意します。Entra SSHでは担当者に対象VM・NIC・Bastionの`Reader`が別途必要で、対象VNetとBastionのVNetが別の場合は対象VNetの`Reader`も必要です。ネイティブCLI接続ではネイティブクライアントを有効にしたStandard以上を使用します。Readerの付与・回収も保守記録に含めます。 [BastionのEntra認証要件](https://learn.microsoft.com/en-us/azure/bastion/bastion-entra-id-authentication)

## 付与と接続

1. 一時的なネットワーク許可が必要な環境では、許可を設定し、TCP 22に届くことを確認します。WindowsではPowerShellで`Test-NetConnection -ComputerName '<MANAGEMENT_VM_PRIVATE_IP>' -Port 22`を実行できます。この状態で、担当者が新規Entra SSH接続を試し、認可で拒否されることを確認します。IAMの「アクセスの確認」で、グループ経由や上位スコープから継承したVMログイン権限も確認します。
2. 管理VMのIAMで、担当者本人に組み込みの`Virtual Machine User Login`を対象VMスコープで付与します。一般ログインによる保守操作にsudoは不要です。付与担当者にはロール割り当ての追加・削除ができる権限が必要です。Azure CLIで付与する場合は、以下の単一行を使います。返る割り当てIDを保護された保守記録に控えます。 [Azure CLIでのRBAC割り当て](https://learn.microsoft.com/en-us/azure/role-based-access-control/role-assignments-cli)

   ```text
   az role assignment create --assignee-object-id '<OPERATOR_OBJECT_ID_IN_AZURE_TENANT>' --assignee-principal-type User --role 'Virtual Machine User Login' --scope '<MANAGEMENT_VM_RESOURCE_ID>' --query id --output tsv
   ```

3. 担当者は自身の端末のAzure CLIで対象テナントにサインインし、承認済みの経路から管理VMのプライベートIPに接続します。IPを指定する接続ではVMやNICの列挙権限を追加せずに確認できます。ロール変更は反映を待ち、必要に応じて再サインインします。端末の準拠状態やハイブリッド参加を条件付きアクセスで求める場合は、ポリシーが対象とするWindowsまたはmacOS上のAzure CLIを利用します。WSL上のLinux版CLIで同じ端末条件を満たせるとは扱いません。 [az ssh vm](https://learn.microsoft.com/en-us/cli/azure/ssh#az-ssh-vm) · [RBACの反映と再サインイン](https://learn.microsoft.com/en-us/azure/role-based-access-control/troubleshooting#symptom---role-assignment-changes-are-not-being-detected)

   ```text
   az login --tenant '<AZURE_TENANT_ID>' --allow-no-subscriptions
   az ssh vm --ip '<MANAGEMENT_VM_PRIVATE_IP>'
   ```

4. 最初のSSH内で`id -un`を実行し、VM上の本人のLinuxユーザー名を確認します。保守クライアントを使う場合は、OS管理者が照合した本人のLinuxユーザーを`core-maintenance`グループに作業時間中だけ登録します。担当者はSSHへ再接続して`id -nG`でグループを確認します。OS管理者は`getent passwd '<OPERATOR_LINUX_USER>'`で照合してから、以下のコマンドで登録できます。対象資源の一時権限、ゲスト内での本人認証、SQL保守は[管理VMの保守操作](../operations.md#管理vmから保守データを取得する)を参照します。

   ```bash
   sudo gpasswd --add '<OPERATOR_LINUX_USER>' core-maintenance
   ```

## 回収と実環境での確認

1. 担当者は保守クライアントやSQLの処理を終了します。SQL接続の残りがあれば、SQL管理者が対象セッションを確認して終了します。SQL利用者を作成した場合は、SQL管理者が今回分を回収し、担当者はSSH接続を保った状態で新規のSQL接続が拒否されることを確認します。保守対象のAzure RBACでも今回分を回収し、その資源への新規操作の拒否を確認します。
2. 担当者はゲスト内のAzure CLIでサインアウトし、SSHからログアウトします。残った対象のSSHセッションはOS管理者が終了を確認します。OS管理者は保守クライアント用の一時グループメンバーシップを回収し、権限付与担当は保護記録に控えた今回作成分のVMログイン等の割り当てIDだけを削除します。

   ```text
   az role assignment delete --ids '<CREATED_ROLE_ASSIGNMENT_ID>'
   ```

   ```bash
   sudo gpasswd --delete '<OPERATOR_LINUX_USER>' core-maintenance
   ```

3. TCP到達を保った状態で、RBACの反映後に担当者の新規のEntra SSH接続が認可で拒否されることを確認します。その後ネットワーク担当が一時的なTCP 22の許可を回収し、新規TCP接続も拒否されることを確認します。付与前・保守中・回収後の結果を保護された報告に記録します。Azureへの配備後にこの手順で確認します。

4. SSHの有効設定を担当者のログイン名とローカル復旧管理者名それぞれで確認します。OpenSSHは既定でホーム内の公開鍵ファイルを参照し、一致した場合はEntra拡張の認証コマンドへ進みません。担当者が管理できるローカル公開鍵でログインできる構成では、Azure RBACを削除してもすべての認証経路の回収を確認できません。新規環境で担当者の代替認証の拒否と、復旧管理者の鍵・Serial Consoleを個別に確認します。 [OpenSSHの設定仕様](https://github.com/openssh/openssh-portable/blob/master/sshd_config.5)

空環境から構築した構成では、証明書の無人処理用IDを[専用ACA Job](certificate-jobs.md)へ割り当てます。管理VMで一時的なイメージビルド権限を使う時間帯は、通常のSSH保守と重ねません。

## 復旧時のSerial Console

既存のSerial Console手順は復旧用です。Serial Consoleの利用者にはAzure側の接続権限とVM内のローカル認証情報が必要です。承認された復旧担当が`Virtual Machine Contributor`か相当の権限を確認し、VMのブート診断が有効であることを確認します。カスタムのブート診断ストレージを導入した場合はそのストレージへの要件も確認します。復旧用のローカルパスワードやSSH鍵は、通常の権限付与に含めません。 [Serial Consoleの資格情報と接続手順](../operations.md#serial-consoleのローカル認証を準備する) · [Linux Serial Consoleの公式条件](https://learn.microsoft.com/en-us/troubleshoot/azure/virtual-machines/linux/serial-console-linux)
