#!/usr/bin/env python3
"""Small deterministic LCOV 2.6 differential runner; see CONTRACT.md."""
import argparse, glob, hashlib, json, os, pathlib, subprocess, sys
ROOT=pathlib.Path(__file__).resolve().parents[2]
SOURCE='@SOURCE@'
FIXTURE={
 'one.info':f'TN:alpha\nSF:{SOURCE}\nDA:1,1\nDA:2,0\nend_of_record\n',
 'two.info':f'TN:beta\nSF:{SOURCE}\nDA:1,2\nDA:2,1\nend_of_record\n',
 'empty.info':f'TN:alpha\nSF:{SOURCE}\nDA:1,0\nend_of_record\n',
 'branches-a.info':f'TN:alpha\nSF:{SOURCE}\nDA:1,1\nDA:2,1\nBRDA:2,7,3,1\nBRDA:2,7,8,0\nend_of_record\n',
 'branches-b.info':f'TN:alpha\nSF:{SOURCE}\nDA:1,1\nDA:2,1\nBRDA:2,91,3,2\nBRDA:2,91,8,4\nend_of_record\n',
 'functions.info':f'TN:alpha\nSF:{SOURCE}\nDA:1,1\nFN:1,oldname\nFNDA:2,oldname\nFNL:0,1\nFNA:0,2,newname\nend_of_record\n',
 'mcdc-a.info':f'TN:alpha\nSF:{SOURCE}\nDA:10,1\nMCDC:10,2,f,0,0,enable\nMCDC:10,2,t,1,0,enable\nend_of_record\n',
 'mcdc-b.info':f'TN:alpha\nSF:{SOURCE}\nDA:10,2\nMCDC:10,2,f,1,0,enable\nMCDC:10,2,t,0,0,enable\nend_of_record\n',
 'checksum-a.info':f'TN:alpha\nSF:{SOURCE}\nDA:1,1,aaaa\nend_of_record\n',
 'checksum-b.info':f'TN:alpha\nSF:{SOURCE}\nDA:1,2,bbbb\nend_of_record\n',
 'malformed.info':f'TN:alpha\nSF:{SOURCE}\nDA:x,1\nend_of_record\n',
}
CASES=[
 ('tn-split',['--no-function-coverage','-a','one.info','-a','two.info']),
 ('duplicate-input',['--no-function-coverage','-a','one.info','-a','one.info']),
 ('empty-section',['--no-function-coverage','-a','empty.info']),
 ('branch-canonical',['--branch-coverage','--no-function-coverage','-a','branches-a.info','-a','branches-b.info']),
 ('function-alias',['--function-coverage','--no-branch-coverage','-a','functions.info']),
 ('mcdc',['--mcdc-coverage','--no-function-coverage','--no-branch-coverage','-a','mcdc-a.info','-a','mcdc-b.info']),
 ('checksum-conflict',['--checksum','--no-function-coverage','-a','checksum-a.info','-a','checksum-b.info']),
 ('malformed',['--no-function-coverage','-a','malformed.info']),
 ('glob',['--no-function-coverage','-a','shard-*.info']),
 ('strict-inconsistent',['--rc','check_data_consistency=1','--branch-coverage','--no-function-coverage','-a','inconsistent.info']),
]
def sha(b): return hashlib.sha256(b).hexdigest()
def normalized(data):
 # Canonicalize line endings and non-function record order within each section while retaining
 # every record value, section order, testcase/source identity and modern aliases.
 text=data.decode('utf-8','surrogateescape').replace('\r\n','\n').replace('\r','\n')
 out=[]; section=[]
 for line in text.splitlines():
  if line=='end_of_record':
   lead=[x for x in section if x.startswith(('TN:','SF:','VER:'))]
   funcs=[x for x in section if x.startswith(('FN:','FNDA:','FNL:','FNA:'))]
   rest=sorted(x for x in section if x not in lead and x not in funcs)
   out.append(tuple(lead+funcs+rest)); section=[]
  else: section.append(line)
 if section: out.append(tuple(section))
 return tuple(out)
def run(cmd,cwd): return subprocess.run(cmd,cwd=cwd,capture_output=True)
def main():
 p=argparse.ArgumentParser(); p.add_argument('--binary'); p.add_argument('--oracle-only',action='store_true'); p.add_argument('--workdir',default='.cache/lcov-compat-oracle'); p.add_argument('--lcov',default='/opt/homebrew/bin/lcov'); a=p.parse_args()
 if not a.oracle_only and not a.binary: p.error('use --binary PATH or --oracle-only')
 wd=(ROOT/a.workdir).resolve(); wd.mkdir(parents=True,exist_ok=True); ident=subprocess.run([sys.executable,str(ROOT/'tools/lcov-oracle-identity.py'),'--lcov',a.lcov,'--strict'],cwd=ROOT,capture_output=True)
 if ident.returncode: sys.stderr.buffer.write(ident.stdout+ident.stderr); return 2
 manifest={'identity':json.loads(ident.stdout),'cases':[]}; failures=0
 for name,opts in CASES:
  d=wd/name; d.mkdir(parents=True,exist_ok=True)
  src=(d/'sample.c').resolve(); src.write_text('\n'.join(['int sample_%d = %d;' % (i, i) for i in range(1, 13)]) + '\n')
  fixture={k:v.replace(SOURCE,str(src)) for k,v in FIXTURE.items()}
  for fn,content in fixture.items(): (d/fn).write_bytes(content.encode())
  if name=='strict-inconsistent':
   (d/'inconsistent.info').write_text(f'TN:alpha\nSF:{src}\nDA:1,1\nBRDA:2,7,3,1\nend_of_record\n')
  if name=='glob':
   (d/'shard-0.info').write_bytes(fixture['one.info'].encode())
   (d/'shard-1.info').write_bytes(fixture['two.info'].encode())
  (d/'lcovrc').write_text('')
  out=d/'oracle.info'; args=[a.lcov,'--config-file',str(d/'lcovrc'),*opts,'-o',str(out)]
  out.unlink(missing_ok=True)
  used=sorted({x for i,opt in enumerate(opts[:-1]) if opt in ('-a','--add-tracefile') for x in glob.glob(opts[i+1],root_dir=d)})
  used_hashes={x:sha((d/x).read_bytes()) for x in used}
  oracle=run(args,d); oracle_bytes=out.read_bytes() if out.exists() else b''
  entry={'name':name,'argv':args,'oracle_status':oracle.returncode,'oracle_stdout_sha256':sha(oracle.stdout),'oracle_stderr_sha256':sha(oracle.stderr),'oracle_stderr':oracle.stderr.decode('utf-8','replace'),'oracle_output_sha256':sha(oracle_bytes),'oracle_output':oracle_bytes.decode('utf-8','surrogateescape') if oracle.returncode==0 else None,'input_hashes':used_hashes,'candidate_status':None,'match':None}
  if a.binary:
   cand=d/'candidate.info'; cand.unlink(missing_ok=True); cargs=[a.binary,'--lcov-compat=2.6',*opts,'-o',str(cand)]; cr=run(cargs,d); cb=cand.read_bytes() if cand.exists() else b''
   entry.update(candidate_argv=cargs,candidate_status=cr.returncode,candidate_output=cb.decode('utf-8','surrogateescape') if cr.returncode==0 else None,candidate_stderr=cr.stderr.decode('utf-8','replace'),match=cr.returncode==oracle.returncode and (cr.returncode!=0 or normalized(cb)==normalized(oracle_bytes)))
   if not entry['match']: failures+=1; print(f'FAIL {name}: oracle={oracle.returncode}, candidate={cr.returncode}',file=sys.stderr)
  manifest['cases'].append(entry)
 (wd/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
 print(json.dumps({'workdir':str(wd),'cases':len(CASES),'failures':failures,'manifest':str(wd/'manifest.json')},indent=2))
 return 1 if failures else 0
if __name__=='__main__': raise SystemExit(main())
