"""Native localhost-only execution guard, not an OS security sandbox."""
import secrets,hashlib,hmac,base64,email,http.cookies,logging,uuid,contextlib,types,sys,os,resource,runpy,socket,sqlite3,http.server,argparse,json,math,csv,io,urllib.parse,threading,datetime,time,pathlib,re,unicodedata,html
from pathlib import Path
root=Path(sys.argv[1]).resolve(); port=int(sys.argv[2]);db=Path(sys.argv[3]).resolve()
stdlib=Path(os.__file__).resolve().parent
resource.setrlimit(resource.RLIMIT_CPU,(90,90));resource.setrlimit(resource.RLIMIT_AS,(1024**3,1024**3));resource.setrlimit(resource.RLIMIT_FSIZE,(16*1024**2,16*1024**2))
def audit(event,args):
 if event=='sqlite3.connect' and args[0]!=':memory:' and not Path(args[0]).resolve().is_relative_to(db.parent):raise PermissionError('database outside assigned data')
 if event in ('sqlite3.enable_load_extension','sqlite3.load_extension'):raise PermissionError('SQLite extensions disabled')
 if event in ('subprocess.Popen','os.system','os.exec','ctypes.dlopen','socket.connect','socket.getaddrinfo'):raise PermissionError('outbound network/process disabled')
 if event=='socket.bind' and args[1]!=('127.0.0.1',port):raise PermissionError('only assigned loopback port allowed')
 if event=='open':
  p=args[0]
  if isinstance(p,int):return
  p=Path(p).resolve();mode=args[1] or '';flags=args[2]
  writing=any(c in mode for c in 'wax+') or bool(flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC))
  allowed=p.is_relative_to(db.parent)
  if not writing:allowed=allowed or p.is_relative_to(root) or p.is_relative_to(stdlib) or str(p) in ('/dev/null','/dev/urandom')
  if not allowed:raise PermissionError('file outside assigned paths')
 if event in ('os.remove','os.rename','os.rmdir','os.mkdir'):
  for p in args[:2]:
   if isinstance(p,(str,bytes)) and not Path(p).resolve().is_relative_to(db.parent):raise PermissionError('write outside assigned data')
sys.path.insert(0,str(root));sys.argv=[str(root/'app.py'),'--port',str(port),'--db',str(db)]
sys.addaudithook(audit);runpy.run_path(str(root/'app.py'),run_name='__main__')
