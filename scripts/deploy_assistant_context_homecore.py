#!/usr/bin/env python3
# ruff: noqa: E501 -- Embedded remote Python is sent unchanged through SSH.
"""Build or deploy the assistant-context API on the exact live home-core image."""

import argparse
import base64
import hashlib
import json
import re
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
CA_SOURCE = "/srv/state/control-plane/caddy-data/caddy/pki/authorities/local/root.crt"
CA_TARGET = "/etc/homecompute/daythread-assistant-context-ca.crt"
CA_SHA256 = "279cbaa0596ae3ca24c44da28ac658c557e3c2a8664f81d8d3c8d034c0753a56"


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def make_payload(root: Path, command: str) -> dict:
    revision = _git(root, "rev-parse", "HEAD")
    files = {name: (root / name).read_bytes() for name in FILES}
    hashes = {name: _sha256(value) for name, value in files.items()}
    digest = _sha256(json.dumps(hashes, sort_keys=True).encode())
    if command in {"stage", "deploy", "credentials-apply", "ca-apply", "backup-fixture-check"}:
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
        "ca_source": CA_SOURCE,
        "ca_target": CA_TARGET,
        "ca_sha256": CA_SHA256,
    }


REMOTE = r'''
import base64,hashlib,hmac,json,os,pathlib,pwd,secrets,shutil,ssl,stat,subprocess,sys,tempfile,time,urllib.error,urllib.request,zipfile
p=json.load(sys.stdin); root=pathlib.Path('/home/mads/projects/omi')
base=root/'releases'/p['base_release']; release=root/'releases'/p['release']; image='omi-local:'+p['release']
container='omi-local-backend-1'
def run(*args,timeout=1800,env=None): subprocess.run(args,check=True,timeout=timeout,stdout=subprocess.DEVNULL,env=env)
def out(*args): return subprocess.check_output(args,text=True,timeout=30).strip()
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def compose(directory,*args):
 c=['docker','compose','-p','omi-local','--env-file',str(directory/'.env'),
    '-f',str(directory/'config/compose.yaml'),'-f',str(directory/'config/compose.home-core.yaml'),
    '-f',str(directory/'config/compose.release.json')]
 overlay=directory/'config/compose.assistant-context.yaml'
 if overlay.is_file(): c.extend(['-f',str(overlay)])
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
def ca_metadata(value):
 path=pathlib.Path(value); info=path.lstat()
 if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=0:
  raise SystemExit('Caddy CA certificate source is unsafe')
 pem=path.read_text(encoding='ascii')
 if 'PRIVATE KEY' in pem: raise SystemExit('Only the public Caddy CA certificate may be copied')
 der=ssl.PEM_cert_to_DER_cert(pem); meta=ssl._ssl._test_decode_cert(str(path))
 common_names=[item for rdn in meta.get('subject',()) for key,item in rdn if key=='commonName']
 if meta.get('subject')!=meta.get('issuer') or common_names!=['Caddy Local Authority - 2026 ECC Root']:
  raise SystemExit('Caddy CA subject or issuer differs from the approved root')
 fingerprint=hashlib.sha256(der).hexdigest()
 if fingerprint!=p['ca_sha256'] or ssl.cert_time_to_seconds(meta['notAfter'])<=time.time():
  raise SystemExit('Caddy CA fingerprint or validity differs from the approved root')
 return {'sha256':fingerprint,'subject':common_names[0],'issuer_matches_subject':True,
         'not_after':meta['notAfter'],'subject_alt_name':meta.get('subjectAltName',[])}
def ca_plan():
 meta=ca_metadata(p['ca_source']); target=pathlib.Path(p['ca_target'])
 try:
  info=target.lstat()
  if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1: raise SystemExit('Existing CA target is unsafe')
  state='present-matching' if ca_metadata(target)['sha256']==meta['sha256'] else 'present-different'
 except FileNotFoundError: state='absent'
 print(json.dumps({'ca_source':p['ca_source'],'ca_target':p['ca_target'],'state':state,**meta},sort_keys=True))
def ca_apply():
 meta=ca_metadata(p['ca_source']); source=pathlib.Path(p['ca_source']); target=pathlib.Path(p['ca_target'])
 parent=target.parent; info=parent.lstat()
 if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid!=0:
  raise SystemExit('CA target parent must remain root-owned')
 try: existing=target.lstat()
 except FileNotFoundError:
  fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o444)
  try:
   os.fchown(fd,0,0); os.fchmod(fd,0o444)
   with os.fdopen(fd,'wb') as stream:
    stream.write(source.read_bytes()); stream.flush(); os.fsync(stream.fileno())
  except BaseException:
   target.unlink(missing_ok=True); raise
  state='created'
 else:
  if not stat.S_ISREG(existing.st_mode) or existing.st_nlink!=1 or ca_metadata(target)['sha256']!=meta['sha256']:
   raise SystemExit('Existing CA target differs; preserve and review it')
  os.chown(target,0,0); os.chmod(target,0o444); state='consistent-existing'
 print(json.dumps({'ca_target':p['ca_target'],'state':state,'sha256':meta['sha256']},sort_keys=True))
def safe_ca():
 path=pathlib.Path(p['ca_target']); info=path.lstat(); meta=ca_metadata(path)
 if info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o444 or meta['sha256']!=p['ca_sha256']:
  raise SystemExit('Installed Daythread CA projection is unsafe or mismatched')
def authenticated_health():
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=p['ca_target'])))
 deadline=time.monotonic()+60
 while time.monotonic()<deadline:
  try:
   with opener.open('https://daythread.home.arpa/v1/status',timeout=4) as r:
    if r.status==200: break
  except Exception: time.sleep(2)
 else: raise SystemExit('Daythread status health check failed')
 token=pathlib.Path(p['token_source']).read_text(encoding='ascii').strip()
 def authenticated(method,path,body=None):
  headers={'Authorization':'Bearer '+token}
  if body is not None: headers['Content-Type']='application/json'
  request=urllib.request.Request('https://daythread.home.arpa'+path,
      data=json.dumps(body,separators=(',',':')).encode() if body is not None else None,
      headers=headers,method=method)
  try:
   with opener.open(request,timeout=12) as response:
    if response.status!=200: raise SystemExit('Authenticated Daythread route check failed')
    raw=response.read(131073)
    if len(raw)>131072: raise SystemExit('Authenticated Daythread response exceeded its bound')
    return response.headers, json.loads(raw)
  except urllib.error.HTTPError:
   raise SystemExit('Authenticated Daythread route check failed') from None
 headers, page=authenticated('GET','/v1/integrations/assistant-context?limit=20')
 if headers.get('Cache-Control')!='no-store' or not isinstance(page,dict) or page.get('source')!='daythread' or page.get('schema_version')!=1:
  raise SystemExit('Authenticated Daythread export metadata is invalid')
 items=page.get('items'); coverage=page.get('coverage')
 if not isinstance(items,list) or len(items)>20 or not isinstance(coverage,dict):
  raise SystemExit('Authenticated Daythread export page is invalid')
 event_counts={}
 object_ids=[]; versioned=0
 for item in items:
  if not isinstance(item,dict) or not isinstance(item.get('object_id'),str):
   raise SystemExit('Authenticated Daythread export item metadata is invalid')
  event=item.get('event_type'); event_counts[event]=event_counts.get(event,0)+1
  object_ids.append(item['object_id'])
  if all(isinstance(item.get(key),str) for key in ('version_ref','source_ref','source_timestamp','modified_at')):
   versioned+=1
 resolver_ids=object_ids or ['diary:2000-01-01']
 _,resolved=authenticated('POST','/v1/integrations/assistant-context/resolve',{'object_ids':resolver_ids})
 if not isinstance(resolved,dict): raise SystemExit('Authenticated Daythread resolve metadata is invalid')
 results=resolved.get('results')
 if resolved.get('schema_version')!=1 or resolved.get('source')!='daythread' or not isinstance(results,list) or len(results)!=len(resolver_ids):
  raise SystemExit('Authenticated Daythread resolve metadata is invalid')
 status_counts={}; resolve_versions=0
 for row in results:
  if not isinstance(row,dict) or row.get('status') not in {'valid','invalidated','expired-window','deleted'}:
   raise SystemExit('Authenticated Daythread resolve status is invalid')
  status=row['status']; status_counts[status]=status_counts.get(status,0)+1
  item=row.get('item')
  if isinstance(item,dict) and all(isinstance(item.get(key),str) for key in ('version_ref','source_ref','source_timestamp','modified_at')):
   resolve_versions+=1
 info=json.loads(out('docker','inspect',container))[0]
 env=dict(item.split('=',1) for item in info['Config']['Env'] if '=' in item)
 expected={'OMI_ASSISTANT_CONTEXT_ENABLED':'true','OMI_ASSISTANT_CONTEXT_PROFILE_ID':'default',
           'OMI_ASSISTANT_CONTEXT_DOMAIN':'personal','OMI_ASSISTANT_CONTEXT_TOKEN_FILE':p['token_target']}
 if any(env.get(k)!=v for k,v in expected.items()): raise SystemExit('Installed provider environment differs from admitted source')
 mount=next((m for m in info['Mounts'] if m.get('Destination')==p['token_target']),None)
 if not mount or mount.get('Source')!=p['token_source'] or mount.get('RW') is not False:
  raise SystemExit('Installed provider token mount differs from source')
 if info['State']['Health']['Status']!='healthy': raise SystemExit('Daythread container is not healthy')
 if asr_hashes()!=p['asr_module_hashes']:
  raise SystemExit('Daythread transcription source hashes changed during provider rollout')
 return {'service':'healthy','export':'enabled','profile':'default','domain':'personal',
         'get_status':200,'get_item_count':len(items),'event_type_counts':event_counts,
         'coverage_window_complete':coverage.get('window_complete'),
         'coverage_deletion_complete':coverage.get('deletion_complete'),
         'snapshot_consistent':page.get('snapshot_consistent'),'get_version_metadata_count':versioned,
         'resolve_status':200,'resolve_status_counts':status_counts,
         'resolve_version_metadata_count':resolve_versions,'resolver_used_synthetic_id':not object_ids,
         'token_mount':'read-only'}
def backup():
 target=root/'backups'/p['release']
 if target.exists():
  if list(target.iterdir()): raise SystemExit('A prior backup artifact exists; preserve and review it')
 else: target.mkdir(mode=0o700,parents=False)
 remote='/data/.exports/.assistant-context-'+p['release']+'-'+str(time.time_ns()); script=remote+'/archive.py'
 archive=target/'daythread.zip'
 backup_config=write_backup_config()
 try:
  compose(release,'stop','--timeout','120','backend')
  code=("from pathlib import Path; import os,stat; e=Path('/data/.exports'); e.mkdir(mode=0o700,exist_ok=True); i=e.lstat(); "
        "assert stat.S_ISDIR(i.st_mode) and not stat.S_ISLNK(i.st_mode) and i.st_uid==os.geteuid() and stat.S_IMODE(i.st_mode)==0o700; "
        "p=Path("+repr(remote)+"); assert not p.exists() and not p.is_symlink(); p.mkdir(mode=0o700); "
        "import runpy,sys; sys.argv=['/tmp/daythread-archive.py','backup',"
        +repr(remote+'/daythread.zip')+",'--data-dir','/data']; "
        "runpy.run_path('/tmp/daythread-archive.py',run_name='__main__')")
  config=['docker','compose','-p','omi-local','--env-file',str(release/'.env'),
   '-f',str(release/'config/compose.yaml'),'-f',str(release/'config/compose.home-core.yaml'),
   '-f',str(release/'config/compose.release.json'),'-f',str(release/'config/compose.assistant-context.yaml'),
   '-f',str(backup_config)]
  run(*config,'run','--rm','--no-deps','-T','--entrypoint','python','backend','-c',code)
  run('docker','cp',container+':'+remote+'/daythread.zip',str(archive))
  with zipfile.ZipFile(archive) as z:
   if 'omi.sqlite3' not in z.namelist() or z.testzip() is not None:
    raise SystemExit('Daythread backup archive validation failed')
  os.chmod(archive,0o600)
  result={'path':str(archive),'bytes':archive.stat().st_size,'sha256':digest(archive)}
 except BaseException:
  backup_config.unlink(missing_ok=True)
  compose(base,'up','-d','--no-deps','--no-build','backend')
  raise
 backup_config.unlink(missing_ok=True)
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
def write_backup_config():
 backup_config=release/'config/compose.assistant-context-backup.yaml'
 if backup_config.exists(): raise SystemExit('A prior backup Compose artifact exists; preserve and review it')
 backup_config.write_text('services:\n  backend:\n    environment:\n'
  '      TMPDIR: /data/.exports\n      TEMP: /data/.exports\n      TMP: /data/.exports\n      SQLITE_TMPDIR: /data/.exports\n'
  '    volumes:\n      - type: bind\n'
  '        source: '+json.dumps(str(release/'scripts/archive.py'))+'\n'
  '        target: /tmp/daythread-archive.py\n        read_only: true\n'
  '        bind:\n          create_host_path: false\n')
 return backup_config
def backup_fixture_check():
 fixture_config=release/'config/compose.assistant-context-backup-fixture.yaml'
 if fixture_config.exists(): raise SystemExit('A prior backup fixture Compose artifact exists; preserve and review it')
 fixture=pathlib.Path(tempfile.mkdtemp(prefix='daythread-backup-fixture-'))
 os.chown(fixture,10001,10001); os.chmod(fixture,0o700)
 backup_config=write_backup_config()
 fixture_config.write_text('services:\n  backend:\n    volumes:\n'
  '      - type: bind\n        source: '+json.dumps(str(fixture))+'\n        target: /data\n'
  '        bind:\n          create_host_path: false\n')
 config=['docker','compose','-p','omi-local','--env-file',str(release/'.env'),
  '-f',str(release/'config/compose.yaml'),'-f',str(release/'config/compose.home-core.yaml'),
  '-f',str(release/'config/compose.release.json'),'-f',str(release/'config/compose.assistant-context.yaml'),
  '-f',str(backup_config),'-f',str(fixture_config)]
 try:
  rendered=json.loads(out(*config,'config','--format','json'))
  mounts=rendered['services']['backend']['volumes']; data=[m for m in mounts if m.get('target')=='/data']
  if len(data)!=1 or data[0].get('source')!=str(fixture): raise SystemExit('Backup fixture Compose does not isolate the live data volume')
  code="""import json,os,pathlib,runpy,sqlite3,tempfile,zipfile
expected='/data/.exports'
assert all(os.environ.get(name)==expected for name in ('TMPDIR','TEMP','TMP','SQLITE_TMPDIR'))
pathlib.Path(expected).mkdir(mode=0o700,parents=True,exist_ok=True)
assert tempfile.gettempdir()==expected
probe=pathlib.Path(expected)/'.assistant-context-backup-temp-probe'
probe.write_bytes(b'ok'); probe.unlink()
from omi_local.store import Store
Store(pathlib.Path('/data'))
with sqlite3.connect('/data/omi.sqlite3') as db:
 db.execute('CREATE TABLE backup_fixture(payload BLOB NOT NULL)')
 db.execute('INSERT INTO backup_fixture VALUES(zeroblob(?))',(20*1024*1024,))
 db.commit()
import sys
sys.argv=['/tmp/daythread-archive.py','backup','/data/.exports/fixture.zip','--data-dir','/data']
runpy.run_path('/tmp/daythread-archive.py',run_name='__main__')
with zipfile.ZipFile('/data/.exports/fixture.zip') as archive:
 assert 'omi.sqlite3' in archive.namelist() and archive.testzip() is None
 assert archive.getinfo('omi.sqlite3').file_size>16*1024*1024
"""
  run(*config,'run','--rm','--no-deps','-T','--entrypoint','python','backend','-c',code)
  print(json.dumps({'fixture':'passed','sqlite_fixture_bytes':20*1024*1024,'tmpfs_tmp_mib':16,
   'temporary_directory':'/data/.exports','live_data_volume_mounted':False},sort_keys=True))
 finally:
  backup_config.unlink(missing_ok=True); fixture_config.unlink(missing_ok=True); shutil.rmtree(fixture)
if p['command']=='plan':
 check_base()
 print(json.dumps({'active_release':p['base_release'],'active_image':p['base_image'],'candidate':p['release'],
  'source_revision':p['revision'],'overlay_sha256':p['overlay_sha256'],'export':'enabled'},sort_keys=True))
elif p['command']=='stage':
 check_base(); stage(); verify_stage()
 print(json.dumps({'staged_release':str(release),'source_revision':p['revision'],'export':'enabled'},sort_keys=True))
elif p['command']=='deploy':
 check_base(); safe_token(); safe_ca(); verify_stage(); saved=backup()
 try:
        compose(release,'up','-d','--no-deps','--no-build','backend'); evidence=authenticated_health()
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
elif p['command']=='backup-fixture-check':
 check_base(); verify_stage(); backup_fixture_check()
elif p['command']=='credentials-plan': credential_plan()
elif p['command']=='credentials-apply': credential_apply()
elif p['command']=='ca-plan': ca_plan()
elif p['command']=='ca-apply': ca_apply()
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "stage", "deploy", "rollback", "credentials-plan", "credentials-apply", "ca-plan", "ca-apply", "backup-fixture-check"])
    parser.add_argument("--host", default="home-core")
    parser.add_argument("--release", help="Exact assistant-context release name for rollback")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        payload = make_payload(root, args.command)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.error(str(error))
    if args.command == "rollback":
        if not args.release or not re.fullmatch(r"20261106-assistant-context-[a-f0-9]{12}", args.release):
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
