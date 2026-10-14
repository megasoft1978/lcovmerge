#!/usr/bin/env python3
"""Emit and optionally enforce the LCOV 2.6 oracle identity."""
import argparse, hashlib, json, os, pathlib, subprocess
EXPECTED = {"release_commit":"a17443e7dbb31b5b7bff6beac32e7b9b02ec3342","version":"LCOV version 2.6-0","files":{"/opt/homebrew/Cellar/lcov/2.6/libexec/bin/lcov":"e609c4ae8870aa5b259052e2c95b45d76fabc63d72c46d4a1ffa73a39cd030df","/opt/homebrew/opt/lcov/lib/lcov/lcovutil.pm":"05299f5a55618cab7271d4bf3ba188937ac9ca4114d248d405959f5f00e97943"}}
def sha(path):
 h=hashlib.sha256()
 with open(path,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''): h.update(b)
 return h.hexdigest()
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--lcov',default='/opt/homebrew/bin/lcov'); ap.add_argument('--strict',action='store_true'); a=ap.parse_args()
 real='/opt/homebrew/Cellar/lcov/2.6/libexec/bin/lcov'; wrapper=os.path.realpath(a.lcov); lib='/opt/homebrew/opt/lcov/lib/lcov/lcovutil.pm'
 version=subprocess.run([real,'--version'],text=True,capture_output=True)
 env=dict(os.environ,PERL5LIB='/opt/homebrew/Cellar/lcov/2.6/lib/lcov')
 xs=subprocess.run(['perl','-I/opt/homebrew/Cellar/lcov/2.6/lib/lcov','-e','require "lcovutil.pm"; print $lcovutil::XS_LOADED'],text=True,capture_output=True,env=env)
 pure=subprocess.run(['perl','-I/opt/homebrew/Cellar/lcov/2.6/lib/lcov','-e','require "lcovutil.pm"; print $lcovutil::XS_LOADED'],text=True,capture_output=True,env=dict(env,LCOV_PURE_PERL='1'))
 files={real:sha(real),lib:sha(lib)}
 out={'release_commit':EXPECTED['release_commit'],'version':version.stdout.strip(),'executable':a.lcov,'resolved_executable':real,'launcher_resolved':wrapper,'launcher_sha256':sha(wrapper),'files_sha256':files,'runtime':subprocess.run(['perl','-v'],text=True,capture_output=True).stdout.splitlines()[1].strip(),'config_sha256':hashlib.sha256(b'').hexdigest(),'backend':{'xs_loaded':xs.stdout,'pure_perl_xs_loaded':pure.stdout},'package_hashes_match_reference':all(files.get(k)==v for k,v in EXPECTED['files'].items()) and EXPECTED['version'] in version.stdout}
 print(json.dumps(out,indent=2))
 if a.strict and (not out['package_hashes_match_reference'] or xs.returncode or pure.returncode or xs.stdout!='1' or pure.stdout!='0'): return 2
 return 0
if __name__=='__main__': raise SystemExit(main())
