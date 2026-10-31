#!/usr/bin/env python3
"""Stage the pinned Daythread gateway-admission fix; deploy only after image mode drains text."""
# ruff: noqa: E501 -- Embedded remote Python is sent unchanged through SSH.
import argparse
import base64
import hashlib
import json
import shlex
import subprocess
from pathlib import Path

BASE_REVISION = "b1e9867f0a4f9426be43490167eca98ab3c7f9dc"
BASE_RELEASE = "20261025-global-topic-review-d1b2a225"
FILES = ["src/omi_local/" + name + ".py" for name in (
    "generation_jobs", "inbox_jobs", "inference_errors", "recording_topics", "settings",
    "weekly", "weekly_store",
)]
ARTIFACTS = ["tests/test_inference_busy_queue.py", "scripts/deploy_busy_retry_homecore.py"]
REMOTE = r'''
import base64,hashlib,json,os,pathlib,shutil,sqlite3,ssl,subprocess,sys,time,urllib.request
payload=json.load(sys.stdin);root=pathlib.Path('/home/mads/projects/omi');base=root/'releases'/payload['base_release']
release=root/'releases'/payload['release'];image='omi-local:'+payload['release']
def run(*args):subprocess.run(args,check=True)
def read(url):
 context=ssl.create_default_context(cafile=str(base/'config/gateway-root.crt'))
 with urllib.request.urlopen(url,context=context,timeout=5)as response:return json.load(response)
def compose(*args):run('docker','compose','-p','omi-local','-f',str(release/'config/compose.yaml'),'-f',str(release/'config/compose.home-core.yaml'),'-f',str(release/'config/compose.release.json'),*args)
if payload['command']=='stage':
 if (root/'current').resolve()!=base:raise SystemExit('Current release changed; review before staging')
 for name,expected in payload['baseline_hashes'].items():
  if hashlib.sha256((base/name).read_bytes()).hexdigest()!=expected:raise SystemExit('Pinned source changed: '+name)
 if release.exists():raise SystemExit('Release exists; retain it and choose a new reviewed artifact')
 shutil.copytree(base,release,symlinks=True)
 for name,encoded in payload['files'].items():
  path=release/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(base64.b64decode(encoded))
 base_image='omi-local:'+payload['base_release']
 base_id=subprocess.check_output(['docker','image','inspect','--format','{{.Id}}',base_image],text=True).strip()
 dockerfile='FROM '+base_image+'\nUSER root\n'
 for name in payload['baseline_hashes']:
  dockerfile+='COPY '+name+' /opt/venv/lib/python3.15/site-packages/omi_local/'+pathlib.Path(name).name+'\n'
 dockerfile+='USER omi\n'
 (release/'Dockerfile.busy-retry').write_text(dockerfile)
 config=json.loads((release/'config/compose.release.json').read_text());backend=config['services']['backend']
 backend['image']=image;backend.setdefault('environment',{})['OMI_LLM_BUSY_RETRY_ENABLED']='true'
 backend['build']['dockerfile']='Dockerfile.busy-retry'
 (release/'config/compose.release.json').write_text(json.dumps(config,indent=2))
 manifest={k:v for k,v in payload.items()if k not in ('files','command')};manifest['base_image_id']=base_id
 (release/'config/busy-retry-artifact.json').write_text(json.dumps(manifest,indent=2))
 os.chmod(release/'config/busy-retry-artifact.json',0o600)
 run('docker','build','-f',str(release/'Dockerfile.busy-retry'),'-t',image,str(release))
 print(json.dumps({'staged_release':str(release),'image':image,'base_image_id':base_id}))
else:
 if not release.exists():raise SystemExit('Stage first')
 manifest=json.loads((release/'config/busy-retry-artifact.json').read_text())
 for name,expected in payload['file_hashes'].items():
  if hashlib.sha256((release/name).read_bytes()).hexdigest()!=expected:raise SystemExit('Staged source differs: '+name)
 if (root/'current').resolve()!=base:raise SystemExit('Current release changed; review before deploying')
 status=read('https://ai.home.arpa/llm/status');comfy=read('https://comfyui.home.arpa/control/api/status')
 if status.get('available')is not False or status.get('active_requests')!=0 or comfy.get('mode')!='image':
  raise SystemExit('Text is not fully drained in image mode; no backend restart performed')
 backup=root/'backups'/payload['release'];backup.mkdir(mode=0o700,exist_ok=False)
 data=pathlib.Path('/var/lib/docker/volumes/omi-local_omi-data/_data')
 for source in data.rglob('*.sqlite3'):
  target=backup/source.relative_to(data);target.parent.mkdir(parents=True,exist_ok=True)
  with sqlite3.connect('file:'+str(source)+'?mode=ro',uri=True)as before,sqlite3.connect(target)as saved:before.backup(saved)
  os.chmod(target,0o600)
 compose('up','-d','--no-deps','--no-build','backend')
 for attempt in range(30):
  try:
   with urllib.request.urlopen('http://192.168.30.122:8787/v1/status',timeout=5)as response:
    if response.status==200:break
  except Exception:
   if attempt==29:raise
   time.sleep(1)
 previous=root/'previous.busy-retry';temp=root/'current.busy-retry'
 if previous.is_symlink():previous.unlink()
 previous.symlink_to(base)
 if temp.is_symlink():temp.unlink()
 temp.symlink_to(release);temp.replace(root/'current')
 print(json.dumps({'deployed_release':str(release),'image':image,'health':'ready'}))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["stage", "deploy"])
    parser.add_argument("--host", default="home-core")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    if revision != BASE_REVISION:
        parser.error("Baseline revision changed; review this targeted deployment first")
    files = {name: (root / name).read_bytes() for name in FILES + ARTIFACTS}
    hashes = {name: hashlib.sha256(value).hexdigest() for name, value in files.items()}
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()[:12]
    baseline = {name: hashlib.sha256(subprocess.check_output(
        ["git", "show", "HEAD:" + name], cwd=root,
    )).hexdigest() for name in FILES}
    payload = {"command": args.command, "base_revision": revision, "base_release": BASE_RELEASE,
               "release": "20261026-gateway-busy-" + digest, "baseline_hashes": baseline,
               "file_hashes": hashes,
               "files": {name: base64.b64encode(value).decode() for name, value in files.items()}}
    subprocess.run(["ssh", args.host, "sudo python3 -c " + shlex.quote(REMOTE)],
                   input=json.dumps(payload), text=True, check=True)


if __name__ == "__main__":
    main()
