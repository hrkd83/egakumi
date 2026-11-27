"""Private Windows native DACL tests; independent temporary keys, no Azure or environment inputs."""
import base64,json,os,re,shutil,subprocess,sys,unittest,uuid
from pathlib import Path
from tests import REPOSITORY
sys.dont_write_bytecode=True
os.environ['PYTHONDONTWRITEBYTECODE']='1'
_REPOSITORY=REPOSITORY
from deployment.helpers.private_permissions import protect_new_path,check_private_permissions,PrivatePermissionsError
from deployment.helpers.powershell_runtime import locate_powershell,powershell_environment
checks=0
acl_roles=[]
_owned_root=None
def ok(value):
    global checks
    assert value;checks+=1
def rejected(fn):
    try:fn()
    except (ValueError,OSError):ok(True);return
    raise AssertionError('unsafe condition accepted')
def make_dir(p):
    p.mkdir();protect_new_path(p,directory=True)
def write(p,text):
    with p.open('x'):pass
    protect_new_path(p);p.write_text(text,encoding='utf-8')
def public_fixture():
    def field(x):return len(x).to_bytes(4,'big')+x
    return 'ssh-ed25519 '+base64.b64encode(field(b'ssh-ed25519')+field(b'x'*32)).decode()+' synthetic'
def run_native_checks():
    global _owned_root
    assert os.name=='nt'
    from deployment.helpers import ssh_keys as m
    local=_REPOSITORY/'.local'
    if local.exists():check_private_permissions(local,directory=True)
    else:make_dir(local)
    root=local/('ssh-tests-'+uuid.uuid4().hex);make_dir(root)
    initial=root.lstat();_owned_root=(root,initial.st_dev,initial.st_ino,local.resolve())
    public=root/'secrets/management-ssh.pub';private=public.with_suffix('')
    config={'management':{'sshPublicKeyFile':str(public.absolute())}}
    original_check=m.check_private_permissions;original_protect=m.protect_new_path
    def checked(p,**k):
        acl_roles.append('check-directory' if k.get('directory') else 'check-file')
        return original_check(p,**k)
    def protected(p,**k):
        acl_roles.append('protect-directory' if k.get('directory') else 'protect-file')
        return original_protect(p,**k)
    m.check_private_permissions=checked;m.protect_new_path=protected
    native_run=subprocess.run;observed=[]
    def traced(args,**kwargs):
        if '-q' in args and '-f' in args:
            stage=Path(args[args.index('-f')+1]).parent
            check_private_permissions(stage,directory=True);observed.append('protected-before-keygen')
        return native_run(args,**kwargs)
    m.subprocess.run=traced
    try:
        result=m.ensure_management_ssh_key(config,root.absolute())
    finally:m.subprocess.run=native_run
    ok(result['created'] is True and observed==['protected-before-keygen'])
    check_private_permissions(private);check_private_permissions(public);ok(True)
    before=private.stat()
    ok(m.ensure_management_ssh_key(config,root.absolute())['created'] is False and private.stat()==before)
    imported=root/'imported-public'
    write(imported,public_fixture())
    m.subprocess.run=lambda *a,**k:(_ for _ in ()).throw(AssertionError('reuse performed external call'))
    try:ok(m.ensure_management_ssh_key({'management':{'sshPublicKeyFile':str(imported.absolute())}},root.absolute())['created'] is False)
    finally:m.subprocess.run=native_run
    staged=root/'staged';make_dir(staged);key=staged/'key';write(key,'synthetic new key')
    info=key.stat();identity=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)
    key.unlink();write(key,'replacement inode')
    rejected(lambda:m._seal_new_key(key,identity))
    check_private_permissions(key);ok(key.read_text()=='replacement inode')
    # A generated inode failure is blocked before publication.
    def synthetic(args,**kwargs):
        if '-q' not in args or '-f' not in args:return native_run(args,**kwargs)
        p=Path(args[args.index('-f')+1])
        p.write_text('synthetic private marker')
        p.with_suffix('.pub').write_text(public_fixture())
        return subprocess.CompletedProcess(args,0,'synthetic private marker','')
    failure=root/'failure';make_dir(failure)
    fp=failure/'secrets/key.pub'
    config_failure={'management':{'sshPublicKeyFile':str(fp.absolute())}}
    native_acl=m._windows_owner_acl
    m.subprocess.run=synthetic
    m._windows_owner_acl=lambda *a,**k:(_ for _ in ()).throw(PrivatePermissionsError('synthetic protection failure'))
    try:rejected(lambda:m.ensure_management_ssh_key(config_failure,failure.absolute()))
    finally:m.subprocess.run=native_run;m._windows_owner_acl=native_acl
    ok(not fp.exists() and not fp.with_suffix('').exists() and not list(failure.rglob('.management-ssh-*')))
    # Use only a disposable empty directory for the shared-DACL refusal.
    shared_root=root/'shared-root';make_dir(shared_root);shared=shared_root/'secrets';make_dir(shared)
    ps=r"""$ErrorActionPreference='Stop';$p=$env:SYNTHETIC_SHARED_DIRECTORY;$acl=Get-Acl -LiteralPath $p;$sid=[Security.Principal.SecurityIdentifier]::new('S-1-1-0');$rule=[Security.AccessControl.FileSystemAccessRule]::new($sid,[Security.AccessControl.FileSystemRights]::ReadAndExecute,[Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit',[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow);$acl.AddAccessRule($rule);if($null -ne ('System.IO.FileSystemAclExtensions' -as [type])){[IO.FileSystemAclExtensions]::SetAccessControl((Get-Item -LiteralPath $p),$acl)}else{(Get-Item -LiteralPath $p).SetAccessControl($acl)};[Console]::Write('shared-fixture-ready')"""
    exe=locate_powershell();env=powershell_environment(exe);env['SYNTHETIC_SHARED_DIRECTORY']=str(shared.absolute())
    r=native_run([exe,'-NoProfile','-NonInteractive','-Command',ps],capture_output=True,text=True,env=env,timeout=20)
    ok(r.returncode==0 and r.stdout=='shared-fixture-ready')
    def acl():
        r=native_run([exe,'-NoProfile','-NonInteractive','-Command',"[Console]::Write((Get-Acl -LiteralPath $env:SYNTHETIC_SHARED_DIRECTORY).Sddl)"],capture_output=True,text=True,env=env,timeout=20)
        assert r.returncode==0;return r.stdout
    before=acl();attempts=[]
    def prevent_keygen(args,**kwargs):
        if '-q' in args and '-f' in args:attempts.append(1);raise AssertionError('shared generation reached keygen')
        return native_run(args,**kwargs)
    m.subprocess.run=prevent_keygen
    try:rejected(lambda:m.ensure_management_ssh_key({'management':{'sshPublicKeyFile':str((shared/'key.pub').absolute())}},shared_root.absolute()))
    finally:m.subprocess.run=native_run
    ok(not attempts and acl()==before and not list(shared.iterdir()))
    return checks


def cleanup_owned_root():
    if _owned_root is None:
        return
    root,device,inode,base=_owned_root
    if not root.exists():
        return
    from deployment.helpers.private_permissions import regular_path
    current=regular_path(root,directory=True)
    if ((current.st_dev,current.st_ino)!=(device,inode)
            or root.resolve().parent!=base or not re.fullmatch(r'ssh-tests-[a-f0-9]{32}',root.name)):
        raise AssertionError('Synthetic cleanup boundary changed; replacement was preserved')
    shutil.rmtree(root)


@unittest.skipUnless(os.name=='nt','Windows native DACL integration')
class WindowsSshPermissionsTests(unittest.TestCase):
    def test_generation_reuse_failure_and_shared_directory_protection(self):
        global checks, _owned_root
        from deployment.helpers import ssh_keys as module
        checks=0;acl_roles.clear();_owned_root=None
        self.addCleanup(setattr,module,'check_private_permissions',module.check_private_permissions)
        self.addCleanup(setattr,module,'protect_new_path',module.protect_new_path)
        self.addCleanup(setattr,module.subprocess,'run',module.subprocess.run)
        self.addCleanup(cleanup_owned_root)
        self.assertEqual(run_native_checks(),11)


if __name__=='__main__':
    unittest.main()
