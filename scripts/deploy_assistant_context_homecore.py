#!/usr/bin/env python3
# ruff: noqa: E501 -- Embedded remote Python is sent unchanged through SSH.
"""Build or deploy the assistant-context API on the exact live home-core image."""

import argparse
import base64
import hashlib
import json
import shlex
import subprocess
from pathlib import Path

BASE_RELEASE = "20261106-transcription-fc4a4eeddd4f"
BASE_IMAGE = "sha256:f212a9d6bbe931c13d8ae89f786853f14e23cd71973f06b7d1d82251c7010b6f"
BASE_COMPOSE_HASHES = {
    "compose.yaml": "6004405b5bfff9a3d1c25c647ae452af5f6470e1b2b5fd1151e1b873739f35df",
    "compose.home-core.yaml": "2258c151ca8589c3daf5935572004e710a8b21665d373216178a6280f5090598",
    "compose.release.json": "b7a0993e34da8acd6f67b64ba0df59b2ad4867351ad07212ad7fe813f9d39d10",
}
ASR_MODULE_HASHES = {
    "configuration": "31b4f703606dde5af358ffbac21601d7c7839f5665f4d3fd6af3f05dffc8627b",
    "settings": "613174e00f2366d41094c94156378bc27496d65692f435b46390aa7d609bcb1e",
    "transcription": "cfa62108d2b4e4debba2b9b54891c1b9c3c90f45fe60044bdaee634bead928f9",
    "transcription_filter": "95495cef901345f7eee2c5990f8cca2eafccb8d98b525233b11ffb0ea05de44d",
    "recording_transcription_worker": "40cfee544f8781d30213b79d3534698046187e59ce8849046fc78e29fcd01a7d",
    "recording_transcription_client": "d5fd7accff4a33475b4b6634b7aef5b29165f65380f43ec858d36e10b5d51578",
    "recording_transcription_import": "20ce134eb343e80c54f79fcd7445c46e551feb8add3547166e377547b8b8a900",
}
FILES = [
    "src/omi_local/api.py",
    "src/omi_local/assistant_context_api.py",
    "config/compose.assistant-context.yaml",
    "scripts/archive.py",
    "scripts/deploy_assistant_context_homecore.py",
]
TOKEN_SOURCE = "/etc/homecompute/daythread-assistant-context-token"
TOKEN_TARGET = "/run/secrets/daythread-assistant-context-token"
COLLECTOR_SOURCE = "/var/lib/homecompute-assistant/daythread-export-token"


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def make_payload(root: Path, command: str) -> dict:
    revision = _git(root, "rev-parse", "HEAD")
    files = {name: (root / name).read_bytes() for name in FILES}
    hashes = {name: _sha256(value) for name, value in files.items()}
    digest = _sha256(json.dumps(hashes, sort_keys=True).encode())
    if command in {"stage", "deploy", "credentials-apply"}:
        if _git(root, "status", "--porcelain", "--", *FILES):
            raise ValueError("Commit the selected provider source before staging")
        if set(_git(root, "ls-files", "--", *FILES).splitlines()) != set(FILES):
            raise ValueError("All provider deployment inputs must be tracked")
        published = _git(root, "ls-remote", "origin", "refs/heads/main").split()
        if len(published) != 2 or published[0] != revision:
            raise ValueError("Publish this source revision on origin/main before deployment")
    return {
        "command": command,
        "revision": revision,
        "base_release": BASE_RELEASE,
        "base_image": BASE_IMAGE,
        "compose_hashes": BASE_COMPOSE_HASHES,
        "asr_module_hashes": ASR_MODULE_HASHES,
        "release": "20261106-assistant-context-" + digest[:12],
        "overlay_sha256": digest,
        "file_hashes": hashes,
        "files": {name: base64.b64encode(value).decode("ascii") for name, value in files.items()},
        "token_source": TOKEN_SOURCE,
        "token_target": TOKEN_TARGET,
        "collector_source": COLLECTOR_SOURCE,
    }


REMOTE = r'''
import base64,hashlib,hmac,json,os,pathlib,pwd,secrets,shutil,stat,subprocess,sys,time,urllib.error,urllib.request,zipfile
p=json.load(sys.stdin); root=pathlib.Path('/home/mads/projects/omi')
base=root/'releases'/p['base_release']; release=root/'releases'/p['release']; image='omi-local:'+p['release']
container='omi-local-backend-1'
def run(*args,timeout=1800): subprocess.run(args,check=True,timeout=timeout,stdout=subprocess.DEVNULL)
def out(*args): return subprocess.check_output(args,text=True,timeout=30).strip()
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def compose(directory,*args):
 c=['docker','compose','-p','omi-local','--env-file',str(directory/'.env'),
    '-f',str(directory/'config/compose.yaml'),'-f',str(directory/'config/compose.home-core.yaml'),
    '-f',str(directory/'config/compose.release.json'),'-f',str(directory/'config/compose.assistant-context.yaml')]
 run(*c,*args)
def current(): return pathlib.Path(os.path.realpath(root/'current'))
def asr_hashes():
 names=sorted(p['asr_module_hashes'])
 code=('import hashlib,json,pathlib; root=pathlib.Path("/opt/venv/lib/python3.15/site-packages/omi_local");'
       ' names='+repr(names)+'; print(json.dumps({n:hashlib.sha256((root/(n+".py")).read_bytes()).hexdigest() for n in names},sort_keys=True))')
 return json.loads(out('docker','exec',container,'python','-c',code))
def check_base():
 if current()!=base: raise SystemExit('Current Daythread release changed; re-review the exact base')
 info=json.loads(out('docker','inspect',container))[0]
 if info['Image']!=p['base_image']: raise SystemExit('Running Daythread image changed; re-review the exact base')
 for name,expected in p['compose_hashes'].items():
  if digest(base/'config'/name)!=expected: raise SystemExit('Pinned Daythread Compose source changed')
 if out('docker','image','inspect','--format','{{.Id}}','omi-local:'+p['base_release'])!=p['base_image']:
  raise SystemExit('Pinned Daythread base image tag changed')
 if asr_hashes()!=p['asr_module_hashes']:
  raise SystemExit('Live Daythread transcription source hashes differ from the qualified base')
def safe_token():
 provider=pathlib.Path(p['token_source']); collector=pathlib.Path(p['collector_source'])
 def read(path,uid,gid=None):
  try: i=path.lstat()
  except OSError: raise SystemExit('Provision the external Daythread token copies before deployment') from None
  if (not stat.S_ISREG(i.st_mode) or i.st_nlink!=1 or i.st_uid!=uid
      or (gid is not None and i.st_gid!=gid) or stat.S_IMODE(i.st_mode)!=0o600
      or not 40<=i.st_size<=512): raise SystemExit('Daythread token file metadata is unsafe')
  return path.read_bytes().strip()
 account=pwd.getpwnam('homecompute-openclaw')
 a=read(provider,10001,10001); b=read(collector,account.pw_uid,account.pw_gid)
 if not __import__('re').fullmatch(rb'[A-Za-z0-9_-]{40,256}',a) or not hmac.compare_digest(a,b):
  raise SystemExit('Daythread provider and collector credentials do not match')
def credential_plan():
 account=pwd.getpwnam('homecompute-openclaw')
 paths=[pathlib.Path(p['token_source']),pathlib.Path(p['collector_source'])]
 states=[]
 for path,uid,gid in [(paths[0],10001,10001),(paths[1],account.pw_uid,account.pw_gid)]:
  try: i=path.lstat()
  except FileNotFoundError: states.append({'path':str(path),'state':'absent'}); continue
  if not stat.S_ISREG(i.st_mode) or i.st_nlink!=1: raise SystemExit('Existing Daythread token path is unsafe')
  states.append({'path':str(path),'state':'present','uid':i.st_uid,'gid':i.st_gid,'mode':format(stat.S_IMODE(i.st_mode),'04o')})
 if all(x['state']=='absent' for x in states): result='ready-to-create'
 elif all(x['state']=='present' for x in states): result='verify-existing-on-apply'
 else: result='partial-pair-apply-will-refuse'
 print(json.dumps({'credentials':result,'files':states},sort_keys=True))
def credential_apply():
 account=pwd.getpwnam('homecompute-openclaw')
 pairs=[(pathlib.Path(p['token_source']),10001,10001,pathlib.Path('/etc/homecompute')),
        (pathlib.Path(p['collector_source']),account.pw_uid,account.pw_gid,pathlib.Path('/var/lib/homecompute-assistant'))]
 states=[]
 for path,uid,gid,parent in pairs:
  try: i=path.lstat(); states.append(('present',i))
  except FileNotFoundError: states.append(('absent',None))
 if any(state=='absent' for state,_ in states) and not all(state=='absent' for state,_ in states):
  raise SystemExit('Partial Daythread token pair exists; refusing to create or replace either copy')
 if all(state=='present' for state,_ in states):
  values=[]
  for (path,uid,gid,parent),(_,i) in zip(pairs,states):
   if not stat.S_ISREG(i.st_mode) or i.st_nlink!=1 or i.st_uid!=uid or i.st_gid!=gid or stat.S_IMODE(i.st_mode)!=0o600 or not 40<=i.st_size<=512:
    raise SystemExit('Existing Daythread token copy metadata mismatch; preserve and reconcile manually')
   values.append(path.read_bytes().strip())
  if not hmac.compare_digest(values[0],values[1]) or not __import__('re').fullmatch(rb'[A-Za-z0-9_-]{40,256}',values[0]):
   raise SystemExit('Existing Daythread token copies differ; refusing rotation')
  print(json.dumps({'credentials':'consistent-existing','provider_path':str(pairs[0][0]),'collector_path':str(pairs[1][0])},sort_keys=True)); return
 for path,uid,gid,parent in pairs:
  if not parent.exists():
   parent.mkdir(mode=0o755 if uid==10001 else 0o700,parents=True)
   if uid!=10001: os.chown(parent,uid,gid); os.chmod(parent,0o700)
  info=parent.lstat()
  if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode): raise SystemExit('Daythread credential parent is not a directory')
  if uid==10001:
   if info.st_uid!=0: raise SystemExit('Provider credential parent must remain root-owned')
  elif info.st_uid!=uid or stat.S_IMODE(info.st_mode)!=0o700:
   raise SystemExit('Collector credential directory must be owned by its service identity at mode 0700')
 value=(secrets.token_urlsafe(48)+'\n').encode('ascii'); created=[]
 try:
  for path,uid,gid,parent in pairs:
   fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
   created.append(path)
   os.fchown(fd,uid,gid); os.fchmod(fd,0o600)
   with os.fdopen(fd,'wb') as stream:
    stream.write(value); stream.flush(); os.fsync(stream.fileno())
  for parent in {item[3] for item in pairs}:
   fd=os.open(parent,os.O_RDONLY); os.fsync(fd); os.close(fd)
 except BaseException:
  for path in created: path.unlink(missing_ok=True)
  raise
 safe_token()
 print(json.dumps({'credentials':'created','provider_path':str(pairs[0][0]),'collector_path':str(pairs[1][0])},sort_keys=True))
def disabled_health():
 deadline=time.monotonic()+60
 while time.monotonic()<deadline:
  try:
   with urllib.request.urlopen('http://192.168.30.122:8787/v1/status',timeout=4) as r:
    if r.status==200: break
  except Exception: time.sleep(2)
 else: raise SystemExit('Daythread status health check failed')
 for method,path,data in [('GET','/v1/integrations/assistant-context',None),('POST','/v1/integrations/assistant-context/resolve',b'{}')]:
  req=urllib.request.Request('http://192.168.30.122:8787'+path,data=data,method=method,
      headers={'Content-Type':'application/json'} if data is not None else {})
  try:
   with urllib.request.urlopen(req,timeout=4) as r: status=r.status
  except urllib.error.HTTPError as e: status=e.code
  if status!=404: raise SystemExit('Disabled assistant-context route did not return 404')
 info=json.loads(out('docker','inspect',container))[0]
 env=dict(item.split('=',1) for item in info['Config']['Env'] if '=' in item)
 expected={'OMI_ASSISTANT_CONTEXT_ENABLED':'false','OMI_ASSISTANT_CONTEXT_PROFILE_ID':'default',
           'OMI_ASSISTANT_CONTEXT_DOMAIN':'','OMI_ASSISTANT_CONTEXT_TOKEN_FILE':p['token_target']}
 if any(env.get(k)!=v for k,v in expected.items()): raise SystemExit('Installed provider environment differs from disabled source')
 mount=next((m for m in info['Mounts'] if m.get('Destination')==p['token_target']),None)
 if not mount or mount.get('Source')!=p['token_source'] or mount.get('RW') is not False:
  raise SystemExit('Installed provider token mount differs from source')
 if info['State']['Health']['Status']!='healthy': raise SystemExit('Daythread container is not healthy')
 if asr_hashes()!=p['asr_module_hashes']:
  raise SystemExit('Daythread transcription source hashes changed during provider rollout')
 return {'service':'healthy','export':'disabled','get_status':404,'resolve_status':404,'token_mount':'read-only'}
def backup():
 target=root/'backups'/p['release']
 if target.exists():
  if list(target.iterdir()): raise SystemExit('A prior backup artifact exists; preserve and review it')
 else: target.mkdir(mode=0o700,parents=False)
 remote='/data/.exports/.assistant-context-'+p['release']; script=remote+'/archive.py'
 archive=target/'daythread.zip'
 check="from pathlib import Path; p=Path("+repr(remote)+"); assert not p.exists() and not p.is_symlink(); p.mkdir(mode=0o700)"
 run('docker','exec',container,'python','-c',check)
 try:
  run('docker','cp',str(release/'scripts/archive.py'),container+':'+script)
  run('docker','exec',container,'python',script,'backup',remote+'/daythread.zip','--data-dir','/data')
  run('docker','cp',container+':'+remote+'/daythread.zip',str(archive))
  with zipfile.ZipFile(archive) as z:
   if 'omi.sqlite3' not in z.namelist() or z.testzip() is not None:
    raise SystemExit('Daythread backup archive validation failed')
  os.chmod(archive,0o600)
  result={'path':str(archive),'bytes':archive.stat().st_size,'sha256':digest(archive)}
 finally:
  run('docker','exec',container,'rm','-rf',remote)
 return result
def stage():
 if release.exists(): raise SystemExit('Assistant-context release already exists; preserve it')
 shutil.copytree(base,release,symlinks=True)
 for name,encoded in p['files'].items():
  path=release/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(base64.b64decode(encoded))
 dockerfile=('FROM omi-local:'+p['base_release']+'\nUSER root\n'
  'COPY src/omi_local/api.py /opt/venv/lib/python3.15/site-packages/omi_local/api.py\n'
  'COPY src/omi_local/assistant_context_api.py /opt/venv/lib/python3.15/site-packages/omi_local/assistant_context_api.py\n'
  'LABEL org.daythread.assistant-context-overlay="'+p['overlay_sha256']+'"\n'
  'LABEL org.opencontainers.image.revision="'+p['revision']+'"\nUSER omi\n')
 (release/'Dockerfile.assistant-context').write_text(dockerfile)
 config=json.loads((release/'config/compose.release.json').read_text())
 backend=config.get('services',{}).get('backend')
 if not isinstance(backend,dict): raise SystemExit('Pinned backend release config is invalid')
 backend['image']='omi-local:'+p['release']; backend.setdefault('build',{})['dockerfile']='Dockerfile.assistant-context'
 (release/'config/compose.release.json').write_text(json.dumps(config,indent=2)+'\n')
 manifest={k:v for k,v in p.items() if k!='files'}
 (release/'config/assistant-context-artifact.json').write_text(json.dumps(manifest,indent=2)+'\n')
 os.chmod(release/'config/assistant-context-artifact.json',0o600)
 compose(release,'config','--quiet')
 run('docker','build','-f',str(release/'Dockerfile.assistant-context'),'-t','omi-local:'+p['release'],str(release))
def verify_stage():
 manifest=json.loads((release/'config/assistant-context-artifact.json').read_text())
 if manifest.get('revision')!=p['revision'] or manifest.get('overlay_sha256')!=p['overlay_sha256']:
  raise SystemExit('Staged provider artifact identity differs')
 for name,expected in p['file_hashes'].items():
  if digest(release/name)!=expected: raise SystemExit('Staged provider source differs')
 if not out('docker','image','inspect','--format','{{.Id}}','omi-local:'+p['release']).startswith('sha256:'):
  raise SystemExit('Staged provider image is missing')
def pointer(target,suffix):
 temp=root/('current.'+suffix+'.tmp')
 if temp.is_symlink(): temp.unlink()
 temp.symlink_to(target); temp.replace(root/'current')
if p['command']=='plan':
 check_base()
 print(json.dumps({'active_release':p['base_release'],'active_image':p['base_image'],'candidate':p['release'],
  'source_revision':p['revision'],'overlay_sha256':p['overlay_sha256'],'export':'disabled'},sort_keys=True))
elif p['command']=='stage':
 check_base(); stage(); verify_stage()
 print(json.dumps({'staged_release':str(release),'source_revision':p['revision'],'export':'disabled'},sort_keys=True))
elif p['command']=='deploy':
 check_base(); safe_token(); verify_stage(); saved=backup()
 try:
  compose(release,'up','-d','--no-deps','--no-build','backend'); evidence=disabled_health()
 except BaseException:
  compose(base,'up','-d','--no-deps','--no-build','backend'); raise
 prior=root/('previous-assistant-context-'+p['release']); prior.symlink_to(base)
 pointer(release,'assistant-context')
 print(json.dumps({'deployed_release':p['release'],'source_revision':p['revision'],'backup':saved,**evidence},sort_keys=True))
elif p['command']=='rollback':
 if current()!=release: raise SystemExit('Current Daythread release is not this provider overlay')
 prior=root/('previous-assistant-context-'+p['release'])
 if not prior.is_symlink() or prior.resolve()!=base: raise SystemExit('Pinned previous release pointer is missing')
 compose(base,'up','-d','--no-deps','--no-build','backend')
 deadline=time.monotonic()+60
 while time.monotonic()<deadline:
  try:
   with urllib.request.urlopen('http://192.168.30.122:8787/v1/status',timeout=4) as r:
    if r.status==200: break
  except Exception: time.sleep(2)
 else: raise SystemExit('Daythread rollback health check failed')
 pointer(base,'assistant-context-rollback')
 print(json.dumps({'rolled_back_to':p['base_release'],'backup_preserved':True},sort_keys=True))
elif p['command']=='credentials-plan': credential_plan()
elif p['command']=='credentials-apply': credential_apply()
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "stage", "deploy", "rollback", "credentials-plan", "credentials-apply"])
    parser.add_argument("--host", default="home-core")
    parser.add_argument("--release", help="Exact assistant-context release name for rollback")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        payload = make_payload(root, args.command)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.error(str(error))
    if args.command == "rollback":
        if not args.release or not args.release.startswith("20261106-assistant-context-"):
            parser.error("rollback requires --release with the exact assistant-context release name")
        payload["release"] = args.release
    elif args.release:
        parser.error("--release is only valid for rollback")
    subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", args.host,
         "sudo -n python3 -c " + shlex.quote(REMOTE)],
        input=json.dumps(payload), text=True, check=True,
    )


if __name__ == "__main__":
    main()
