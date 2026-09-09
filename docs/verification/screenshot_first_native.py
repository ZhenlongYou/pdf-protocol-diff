"""Exercise the report in native WebKit, including folds and original-size view."""
import hashlib,json,sys,threading,time
from pathlib import Path
import webview
REPORT=Path(sys.argv[1]).resolve();OUT=Path(sys.argv[2]).resolve();OUT.mkdir(parents=True,exist_ok=True)
window=webview.create_window('PDF report screenshot review',url=REPORT.as_uri(),width=1440,height=1000)
result={'report':str(REPORT),'sha256':hashlib.sha256(REPORT.read_bytes()).hexdigest()};errors=[]
def snapshot(name):
 import AppKit
 from PyObjCTools import AppHelper
 from webview.platforms.cocoa import BrowserView
 done=threading.Event();state={}
 def captured(im,error):
  try:
   assert not error,str(error)
   bitmap=AppKit.NSBitmapImageRep.imageRepWithData_(im.TIFFRepresentation())
   data=bitmap.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG,{})
   path=OUT/(name+'.png');assert data.writeToFile_atomically_(str(path),True);state['path']=str(path)
  except BaseException as e:state['error']=repr(e)
  finally:done.set()
 def take():BrowserView.instances[window.uid].webview.takeSnapshotWithConfiguration_completionHandler_(None,captured)
 AppHelper.callAfter(take);assert done.wait(15);assert 'error' not in state,state;return state['path']
def run():
 try:
  assert window.events.loaded.wait(45)
  time.sleep(.3)
  state=window.evaluate_js("""(()=>{const images=[...document.querySelectorAll('.table-shot-page img,.prose-source-page img')];const folds=[...document.querySelectorAll('.prose-text-details,.table-text-details,.similarity-review-appendix')];return {images:images.length,broken:images.filter(i=>!i.complete||!i.naturalWidth).length,openFolds:folds.filter(d=>d.open).length,folds:folds.length,overflow:document.documentElement.scrollWidth>innerWidth}})()""")
  result['initial']=state;assert state['images']>0 and state['broken']==0 and state['openFolds']==0 and not state['overflow'],state
  result['top']=snapshot('top')
  for name,selector in [('table','.table-visual-card'),('prose','.change-card')]:
   found=window.evaluate_js("(()=>{const e=document.querySelector("+json.dumps(selector)+");if(!e)return false;for(let n=e;n;n=n.parentElement)if(n.tagName==='DETAILS')n.open=true;e.scrollIntoView({block:'start'});return true})()")
   if found:time.sleep(.2);result[name]=snapshot(name)
  result['viewer']=window.evaluate_js("""(()=>{const i=document.querySelector('.table-shot-page img,.prose-source-page img');i.click();const d=document.querySelector('.source-image-viewer');return {open:d.open,same:d.querySelector('img').src===i.src}})()""")
  assert result['viewer']=={'open':True,'same':True};time.sleep(.2);result['zoom']=snapshot('zoom')
  window.evaluate_js("document.querySelector('.source-image-viewer button').click()")
  window.resize(520,900);time.sleep(.3)
  assert window.evaluate_js('document.documentElement.scrollWidth<=innerWidth')
  result['narrow']=snapshot('narrow');result['status']='PASS'
 except BaseException as error:errors.append(error);result.update(status='FAIL',error=repr(error))
 finally:(OUT/'layout.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));window.destroy()
webview.start(run,private_mode=True)
print(json.dumps(result,ensure_ascii=False));sys.exit(bool(errors))
